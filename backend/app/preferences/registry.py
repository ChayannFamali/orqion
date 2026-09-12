"""Реестр описаний личных настроек пользователя.

Описания ключей живут в коде, не в БД: таблица ``user_preferences`` хранит
только факт записи значения, поэтому добавление новой личной настройки —
новый элемент реестра, без миграции схемы. Паттерн тот же, что у реестра
служебных настроек (``app/settings/registry.py``), а отличия — в области
действия и в правах:

- строка принадлежит пользователю, а не рабочей области, поэтому
  ``workspace_id`` в ключе записи соседствует с ``user_id``;
- права на ключ нет вовсе: читать и менять свою настройку может любой
  аутентифицированный пользователь, чужие строки ему не видны. Отсюда в
  спеке нет ``write_capability``, а в ответе API ``editable`` всегда
  ``true``;
- источник значения до первой записи один — дефолт модели значения. Поля
  ``default_from_env`` нет: личная настройка не заменяет собой параметр
  конфигурации экземпляра, она описывает предпочтения одного человека.

Контракт значения (единственное поле ``value``, валидация и приведение к
JSON-виду, описание поля для интерфейса) — общий с реестром служебных
настроек, функции берутся оттуда, чтобы оба раздела рисовались одинаково и
не разъезжались.

Дополнительно к описанию поля спека несёт ``option_titles`` — подписи
значений перечисления на языке интерфейса. Машинные значения (``enter``)
пользователю не показываются, а подписи объявляются только полным набором:
частичное покрытие смешало бы в одном списке подписи и сырые значения.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.settings.registry import (
    VALUE_FIELD,
    describe_value_model,
    model_default,
    require_value_field,
)


class PreferenceSpec(BaseModel):
    """Описание одного ключа личных настроек."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    title: str
    description: str
    category: str
    value_model: type[BaseModel]
    option_titles: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_resolvable(self) -> PreferenceSpec:
        """Отказ при создании спеки, а не в момент запроса.

        Все проверки — про опечатки в коде: модель значения обязана
        описывать ровно одно поле ``value`` и иметь дефолт (иначе до первой
        записи значение неоткуда взять), а подписи вариантов обязаны
        покрывать перечисление целиком и не ссылаться на несуществующие
        значения.
        """
        require_value_field(self.key, self.value_model)

        if self.value_model.model_fields[VALUE_FIELD].is_required():
            raise ValueError(
                f"Личная настройка {self.key!r}: у поля {VALUE_FIELD!r} нет значения "
                f"по умолчанию — до первой записи значение неоткуда взять"
            )

        self._check_option_titles()
        return self

    def _check_option_titles(self) -> None:
        enum_values = describe_value_model(self.value_model).enum_values
        if not self.option_titles:
            if enum_values is not None:
                raise ValueError(
                    f"Личная настройка {self.key!r}: значение перечислением "
                    f"{', '.join(enum_values)}, но подписи вариантов не заданы — "
                    f"в интерфейсе оказались бы машинные значения"
                )
            return

        if enum_values is None:
            raise ValueError(
                f"Личная настройка {self.key!r}: подписи вариантов заданы, "
                f"но её значение не перечисление"
            )
        unknown = sorted(set(self.option_titles) - set(enum_values))
        if unknown:
            raise ValueError(
                f"Личная настройка {self.key!r}: подписи заданы для несуществующих "
                f"вариантов {', '.join(unknown)}"
            )
        missing = [value for value in enum_values if value not in self.option_titles]
        if missing:
            raise ValueError(
                f"Личная настройка {self.key!r}: нет подписи для вариантов {', '.join(missing)}"
            )


#: Реестр ключей.
#:
#: Читается маршрутами в момент запроса, а не копируется при импорте,
#: поэтому добавление ключа (в том числе в тесте) подхватывается без
#: перезапуска и без миграции.
PREFERENCES_REGISTRY: dict[str, PreferenceSpec] = {}


# ---------------------------------------------------------------------------
# Ключи реестра
# ---------------------------------------------------------------------------

#: Отправка сообщения клавишей Enter, перенос строки — Shift+Enter.
#:
#: Значения совпадают с литералами модели ``ChatSendKeyValue``: аннотация
#: ``Literal`` нужна, чтобы mypy видел совместимость константы с полем
#: модели, а не ``str``.
SEND_ON_ENTER: Literal["enter"] = "enter"

#: Отправка сообщения клавишами Shift+Enter, перенос строки — Enter.
SEND_ON_SHIFT_ENTER: Literal["shift_enter"] = "shift_enter"


class ChatSendKeyValue(BaseModel):
    """Какое сочетание клавиш отправляет сообщение в чате."""

    value: Literal["enter", "shift_enter"] = SEND_ON_ENTER


#: Ключ личной настройки: способ отправки сообщения.
CHAT_SEND_KEY = "chat_send_key"

#: Категория настроек чата: одна вкладка интерфейса на ключи чата.
CHAT_CATEGORY = "Чат"

PREFERENCES_REGISTRY[CHAT_SEND_KEY] = PreferenceSpec(
    key=CHAT_SEND_KEY,
    title="Отправка сообщения",
    description=(
        "Какое сочетание клавиш отправляет сообщение в чате; второе "
        "сочетание вставляет перенос строки. Действует во всех диалогах, "
        "включая агентные."
    ),
    category=CHAT_CATEGORY,
    value_model=ChatSendKeyValue,
    option_titles={
        SEND_ON_ENTER: "Enter — отправить, Shift+Enter — новая строка",
        SEND_ON_SHIFT_ENTER: "Shift+Enter — отправить, Enter — новая строка",
    },
)


def get_spec(key: str) -> PreferenceSpec | None:
    """Спека ключа или None, если ключ не зарегистрирован."""
    return PREFERENCES_REGISTRY.get(key)


def ordered_specs() -> list[PreferenceSpec]:
    """Спеки в стабильном порядке: категория, затем ключ.

    Порядок нужен ответу API — иначе состав списка зависел бы от порядка
    вставки в словарь, а интерфейс группировал бы разделы по-разному между
    запросами.
    """
    return sorted(PREFERENCES_REGISTRY.values(), key=lambda spec: (spec.category, spec.key))


def default_value(spec: PreferenceSpec) -> JsonValue:
    """Значение ключа до первой записи в БД: дефолт модели значения."""
    return model_default(spec.value_model)
