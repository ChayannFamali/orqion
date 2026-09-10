"""Реестр описаний служебных настроек рабочей области.

Описания ключей живут в коде, не в БД: таблица ``workspace_settings``
хранит только факт записи значения, поэтому добавление новой настройки —
новый элемент реестра, без миграции схемы.

``SettingSpec`` описывает ключ четырьмя существенными частями:

- ``value_model`` — pydantic-модель значения; запись проходит валидацию
  ею, несоответствие даёт 422, а не молча принятое значение;
- ``default_from_env`` — имя поля ``app.config.Settings``, откуда берётся
  значение до первой записи в БД: поведение без записи совпадает с
  текущим env-конфигом;
- ``category`` — вкладка интерфейса; вкладки строятся динамически из
  уникальных категорий ответа, список категорий нигде не продублирован;
- ``write_capability`` — способность на запись именно этого ключа. Право
  проверяется по тегу спеки, а не общим гейтом на маршрут: одна ручка
  обслуживает ключи с разными правами.

Дефолт берётся из ``default_from_env``, а если он не задан — из дефолта
единственного поля ``value`` модели значения. Спека без ни того ни другого
отвергается при создании: настройка без значения до первой записи
неотличима от сломанной.

Содержимое реестра пустое: механизм готов, первые ключи приходят
отдельными задачами. ``GET /api/workspace/settings`` при этом честно
отдаёт пустой список, а интерфейс показывает «Настроек пока нет».
"""

from __future__ import annotations

from types import NoneType, UnionType
from typing import Any, Literal, Union, cast, get_args, get_origin

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from app.config import Settings

#: Имя единственного поля модели значения.
VALUE_FIELD = "value"

#: Типы полей env-конфига, значение которых сериализуется в JSON как есть.
_JSON_ANNOTATIONS: tuple[Any, ...] = (str, int, float, bool)


def _is_json_annotation(annotation: Any) -> bool:
    """True, если значение поля env-конфига сериализуется в JSON как есть.

    Проверка статическая, по аннотации: она отсекает ключ, у которого
    ``default_from_env`` указывает, например, на ``Path`` — иначе отказ
    случился бы при сериализации ответа, уже на бою. Допускается
    необязательный скаляр (``str | None``): ``null`` — корректный JSON.
    Список намеренно короткий: когда понадобится ключ со значением-списком
    или словарём, его расширяют осознанно, вместе с описанием поля для
    интерфейса.
    """
    if annotation in _JSON_ANNOTATIONS:
        return True
    if get_origin(annotation) not in (Union, UnionType):
        return False
    branches = [item for item in get_args(annotation) if item is not NoneType]
    return bool(branches) and all(item in _JSON_ANNOTATIONS for item in branches)


#: Способность на запись ключей реестра первой версии.
#:
#: Wildcard-only и не входит ни в один посевной пресет: право выдаётся
#: явно вместе с ``*``. Отдельные способности на будущие категории ключей
#: появятся вместе с ключами, когда будет видно, где право действительно
#: должно стать мельче.
DEFAULT_WRITE_CAPABILITY = "manage_settings"

#: Тип значения в терминах интерфейса.
UiValueType = Literal["integer", "number", "boolean", "string", "enum"]

_UI_TYPE_BY_JSON_TYPE: dict[str, UiValueType] = {
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "string": "string",
}


class ValueFieldDescription(BaseModel):
    """Как рисовать поле значения: тип, опции перечисления и границы.

    Производное от pydantic-модели значения описание — единственный
    источник, из которого фронтенд узнаёт тип поля. Границы берутся из
    inklusивных ``minimum``/``maximum`` JSON Schema (``Field(ge=…, le=…)``);
    эксклюзивные границы не переводятся в подсказку намеренно: значение за
    ними всё равно отсекает серверная валидация, а неточная подсказка
    врала бы пользователю.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: UiValueType
    enum_values: list[str] | None = None
    min: float | None = None
    max: float | None = None


class SettingSpec(BaseModel):
    """Описание одного ключа служебных настроек."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    title: str
    description: str
    category: str
    value_model: type[BaseModel]
    default_from_env: str | None = None
    write_capability: str = DEFAULT_WRITE_CAPABILITY

    @model_validator(mode="after")
    def _check_resolvable(self) -> SettingSpec:
        """Отказ при создании спеки, а не в момент запроса.

        Все проверки — про опечатки в коде: модель значения обязана
        описывать ровно одно поле ``value`` (иначе непонятно, что
        валидировать и что возвращать), имя поля env-конфига обязано
        существовать и быть JSON-совместимым, а при его отсутствии у поля
        значения обязан быть дефолт.
        """
        fields = set(self.value_model.model_fields)
        if fields != {VALUE_FIELD}:
            raise ValueError(
                f"Модель значения настройки {self.key!r} должна содержать ровно одно "
                f"поле {VALUE_FIELD!r}, найдено: {sorted(fields)}"
            )

        if self.default_from_env is not None:
            field = Settings.model_fields.get(self.default_from_env)
            if field is None:
                raise ValueError(
                    f"Настройка {self.key!r}: в app.config.Settings нет поля "
                    f"{self.default_from_env!r}"
                )
            if not _is_json_annotation(field.annotation):
                raise ValueError(
                    f"Настройка {self.key!r}: поле env-конфига "
                    f"{self.default_from_env!r} имеет тип {field.annotation!r}, "
                    f"который не сериализуется в JSON как есть"
                )
        elif self.value_model.model_fields[VALUE_FIELD].is_required():
            raise ValueError(
                f"Настройка {self.key!r}: не задан ни default_from_env, ни дефолт "
                f"поля {VALUE_FIELD!r} — значение до первой записи неоткуда взять"
            )
        return self


#: Реестр ключей.
#:
#: Читается маршрутами в момент запроса, а не копируется при импорте,
#: поэтому добавление ключа (в том числе в тесте) подхватывается без
#: перезапуска и без миграции.
SETTINGS_REGISTRY: dict[str, SettingSpec] = {}


def get_spec(key: str) -> SettingSpec | None:
    """Спека ключа или None, если ключ не зарегистрирован."""
    return SETTINGS_REGISTRY.get(key)


def ordered_specs() -> list[SettingSpec]:
    """Спеки в стабильном порядке: категория, затем ключ.

    Порядок нужен ответу API — иначе состав списка зависел бы от порядка
    вставки в словарь, а интерфейс группировал бы вкладки по-разному между
    запросами.
    """
    return sorted(SETTINGS_REGISTRY.values(), key=lambda spec: (spec.category, spec.key))


def default_value(spec: SettingSpec, settings: Settings) -> JsonValue:
    """Значение ключа до первой записи в БД: env-конфиг или дефолт модели.

    Приведение безопасно: тип поля env-конфига проверен при создании спеки
    (``_JSON_ANNOTATIONS``), а дефолт модели значения проходит через неё же.
    """
    if spec.default_from_env is not None:
        return cast(JsonValue, getattr(settings, spec.default_from_env))
    return cast(JsonValue, spec.value_model.model_fields[VALUE_FIELD].get_default())


def coerce_value(spec: SettingSpec, raw: JsonValue) -> JsonValue:
    """Проверяет значение моделью спеки и возвращает его в JSON-виде.

    ``ValidationError`` не перехватывается: маршрут переводит его в 422,
    а здесь исключение означает ровно «значение не прошло валидацию».
    Возвращается приведённое значение (``mode="json"``), а не сырое —
    иначе в БД и в аудит легли бы, например, член перечисления вместо
    строки или ``5.0`` вместо ``5``.
    """
    validated = spec.value_model.model_validate({VALUE_FIELD: raw})
    return cast(JsonValue, validated.model_dump(mode="json")[VALUE_FIELD])


def describe_value_field(spec: SettingSpec) -> ValueFieldDescription:
    """Описание поля значения для интерфейса — из JSON Schema модели."""
    prop = _value_property_schema(spec.value_model)
    prop = _unwrap_alternatives(prop)

    enum_values = prop.get("enum")
    if enum_values is not None:
        return ValueFieldDescription(
            type="enum",
            enum_values=[str(item) for item in enum_values],
        )

    json_type = prop.get("type")
    if isinstance(json_type, list):
        json_type = next((item for item in json_type if item != "null"), None)
    minimum = prop.get("minimum")
    maximum = prop.get("maximum")
    return ValueFieldDescription(
        type=_UI_TYPE_BY_JSON_TYPE.get(str(json_type or ""), "string"),
        min=float(minimum) if minimum is not None else None,
        max=float(maximum) if maximum is not None else None,
    )


def _value_property_schema(value_model: type[BaseModel]) -> dict[str, Any]:
    schema = value_model.model_json_schema()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    prop = properties.get(VALUE_FIELD)
    return dict(prop) if isinstance(prop, dict) else {}


def _unwrap_alternatives(prop: dict[str, Any]) -> dict[str, Any]:
    """Первая не-``null`` ветка ``anyOf``/``oneOf`` как описание поля.

    Нужно для моделей вида ``value: int | None``: JSON Schema описывает их
    перечислением веток, а интерфейсу нужен один тип. Ветки сливаются в
    один словарь, чтобы ограничения из разных веток не терялись.
    """
    alternatives = prop.get("anyOf") or prop.get("oneOf")
    if not isinstance(alternatives, list):
        return prop

    merged: dict[str, Any] = {k: v for k, v in prop.items() if k not in {"anyOf", "oneOf"}}
    for branch in alternatives:
        if not isinstance(branch, dict) or branch.get("type") == "null":
            continue
        merged.update(branch)
    return merged
