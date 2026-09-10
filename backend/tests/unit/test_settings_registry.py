"""Реестр описаний служебных настроек: спеки, дефолты, валидация, описание поля."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import pytest
from app.config import Settings
from app.policy.presets import BUILTIN_ROLES
from app.settings.registry import (
    DEFAULT_WRITE_CAPABILITY,
    SESSION_TTL_KEY,
    SETTINGS_REGISTRY,
    SettingSpec,
    _is_json_annotation,
    coerce_value,
    default_value,
    describe_value_field,
    get_spec,
    ordered_specs,
)
from pydantic import BaseModel, Field, ValidationError

# ---------------------------------------------------------------------------
# Модели значений
# ---------------------------------------------------------------------------


class IntValue(BaseModel):
    value: int = Field(default=30, ge=1, le=365)


class BoolValue(BaseModel):
    value: bool = False


class StrValue(BaseModel):
    value: str = "текст"


class ModeValue(BaseModel):
    value: Literal["fast", "balanced", "thorough"] = "balanced"


class OptionalIntValue(BaseModel):
    value: int | None = None


class DefaultValue(BaseModel):
    value: int = 5


class RequiredValue(BaseModel):
    value: int


class TwoFields(BaseModel):
    value: int = 1
    other: int = 2


class FloatValue(BaseModel):
    value: float = Field(default=0.7, ge=0.0, le=2.0)


def _spec(**overrides: Any) -> SettingSpec:
    """Спека с разрешимым значением: модель по умолчанию имеет дефолт."""
    base: dict[str, Any] = {
        "key": "test_key",
        "title": "Заголовок",
        "description": "Описание",
        "category": "Общие",
        "value_model": DefaultValue,
    }
    base.update(overrides)
    return SettingSpec(**base)


@pytest.fixture
def registry() -> Iterator[dict[str, SettingSpec]]:
    """Временно очищает реестр: тесты не должны оставлять в нём ключей."""
    saved = dict(SETTINGS_REGISTRY)
    SETTINGS_REGISTRY.clear()
    yield SETTINGS_REGISTRY
    SETTINGS_REGISTRY.clear()
    SETTINGS_REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Валидация спеки при создании
# ---------------------------------------------------------------------------


def test_spec_rejects_value_model_without_single_value_field() -> None:
    """Модель значения обязана описывать ровно одно поле value."""
    with pytest.raises(ValidationError) as exc:
        _spec(value_model=TwoFields)
    assert "ровно одно поле" in str(exc.value)


def test_spec_rejects_unknown_env_field() -> None:
    """Опечатка в имени поля env-конфига отсекается при создании спеки."""
    with pytest.raises(ValidationError) as exc:
        _spec(value_model=RequiredValue, default_from_env="no_such_field_in_settings")
    assert "no_such_field_in_settings" in str(exc.value)


def test_spec_rejects_non_json_env_field() -> None:
    """Поле env-конфига, не сериализуемое в JSON как есть, отсекается сразу.

    Сегодня в ``Settings`` таких полей нет (все — скаляры), поэтому отказ
    проверяется на синтетической аннотации: иначе он всплыл бы 500-м при
    сериализации ответа.
    """
    assert _is_json_annotation(Path) is False
    assert _is_json_annotation(list[str]) is False
    assert _is_json_annotation(dict[str, int]) is False


def test_is_json_annotation_accepts_scalars_and_optional() -> None:
    """null — корректный JSON, поэтому необязательный скаляр допустим."""
    for annotation in (str, int, float, bool, str | None, int | None):
        assert _is_json_annotation(annotation) is True, annotation


def test_spec_rejects_missing_default() -> None:
    """Без default_from_env у поля значения обязан быть дефолт."""
    with pytest.raises(ValidationError) as exc:
        _spec(value_model=RequiredValue)
    assert "default_from_env" in str(exc.value)


def test_spec_accepts_env_default_and_model_default() -> None:
    assert _spec(value_model=RequiredValue, default_from_env="session_ttl_days").key == "test_key"
    assert _spec(value_model=DefaultValue).key == "test_key"


def test_spec_defaults_write_capability_to_manage_settings() -> None:
    assert _spec().write_capability == DEFAULT_WRITE_CAPABILITY == "manage_settings"


def test_write_capability_absent_from_builtin_presets() -> None:
    """Способность wildcard-only: ни один посевной пресет её не выдаёт явно."""
    for name, policy in BUILTIN_ROLES.items():
        assert DEFAULT_WRITE_CAPABILITY not in policy.capabilities, name


def test_spec_forbids_extra_fields() -> None:
    """Неизвестное поле спеки отсекается: опечатка не должна молча проходить."""
    with pytest.raises(ValidationError):
        SettingSpec(
            key="k",
            title="t",
            description="d",
            category="c",
            value_model=DefaultValue,
            unknown=1,  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Резолв значения
# ---------------------------------------------------------------------------


def test_default_value_comes_from_env_config() -> None:
    spec = _spec(value_model=RequiredValue, default_from_env="session_ttl_days")
    assert default_value(spec, Settings(session_ttl_days=21)) == 21


def test_default_value_comes_from_model_default_when_no_env_field() -> None:
    spec = _spec(value_model=DefaultValue)
    assert default_value(spec, Settings(session_ttl_days=21)) == 5


def test_env_default_wins_over_model_default() -> None:
    spec = _spec(value_model=DefaultValue, default_from_env="session_ttl_days")
    assert default_value(spec, Settings(session_ttl_days=3)) == 3


def test_coerce_value_rejects_wrong_type() -> None:
    with pytest.raises(ValidationError):
        coerce_value(_spec(), "не число")


def test_coerce_value_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        coerce_value(_spec(value_model=IntValue), 366)


def test_coerce_value_returns_json_form() -> None:
    """В БД и аудит ложится приведённое значение, а не сырой объект."""
    assert coerce_value(_spec(value_model=ModeValue), "fast") == "fast"
    with pytest.raises(ValidationError):
        coerce_value(_spec(value_model=ModeValue), "turbo")


def test_coerce_value_accepts_boolean_and_string() -> None:
    assert coerce_value(_spec(value_model=BoolValue), True) is True
    assert coerce_value(_spec(value_model=StrValue), "текст") == "текст"


# ---------------------------------------------------------------------------
# Описание поля для интерфейса
# ---------------------------------------------------------------------------


def test_describe_integer_field_with_bounds() -> None:
    field = describe_value_field(_spec(value_model=IntValue))
    assert field.type == "integer"
    assert (field.min, field.max) == (1.0, 365.0)
    assert field.enum_values is None


def test_describe_float_field_as_number() -> None:
    field = describe_value_field(_spec(value_model=FloatValue))
    assert field.type == "number"
    assert (field.min, field.max) == (0.0, 2.0)


def test_describe_boolean_and_string_fields() -> None:
    assert describe_value_field(_spec(value_model=BoolValue)).type == "boolean"
    text = describe_value_field(_spec(value_model=StrValue))
    assert text.type == "string"
    assert (text.min, text.max, text.enum_values) == (None, None, None)


def test_describe_literal_as_enum_with_options() -> None:
    field = describe_value_field(_spec(value_model=ModeValue))
    assert field.type == "enum"
    assert field.enum_values == ["fast", "balanced", "thorough"]


def test_describe_optional_field_keeps_type_and_bounds() -> None:
    """`int | None` описывается одной веткой anyOf, тип не теряется."""
    field = describe_value_field(_spec(value_model=OptionalIntValue))
    assert field.type == "integer"
    assert field.enum_values is None


# ---------------------------------------------------------------------------
# Реестр
# ---------------------------------------------------------------------------


def test_get_spec_returns_none_for_unregistered_key(registry: dict[str, SettingSpec]) -> None:
    assert get_spec("нет_такого_ключа") is None


def test_registry_lookup_and_ordering(registry: dict[str, SettingSpec]) -> None:
    """Категория главнее ключа: сортировка по (category, key).

    Набор подобран так, чтобы порядок по одному ключу дал бы другой
    результат (``a_key`` попал бы первым) — иначе тест ничего не проверял.
    """
    registry["z_key"] = _spec(key="z_key", category="Вторая")
    registry["a_key"] = _spec(key="a_key", category="Первая")
    registry["y_key"] = _spec(key="y_key", category="Вторая")

    assert get_spec("z_key") is registry["z_key"]
    assert [spec.key for spec in ordered_specs()] == ["y_key", "z_key", "a_key"]


def test_session_ttl_key_is_registered() -> None:
    """Первый рабочий ключ реестра: срок жизни сессии."""
    spec = get_spec(SESSION_TTL_KEY)
    assert spec is not None
    assert spec.category == "Сессии и безопасность"
    assert spec.default_from_env == SESSION_TTL_KEY
    assert spec.write_capability == DEFAULT_WRITE_CAPABILITY
    field = describe_value_field(spec)
    assert field.type == "integer"
    assert (field.min, field.max) == (1.0, 365.0)


def test_every_registered_key_is_presentable() -> None:
    """У каждого ключа есть что показать в интерфейсе и откуда взять дефолт.

    Проверка идёт по фактическому содержимому реестра, поэтому новый ключ
    без заголовка, описания, категории или разрешимого дефолта падает здесь,
    а не пустым полем в интерфейсе.
    """
    assert SETTINGS_REGISTRY, "реестр не должен быть пустым"
    settings = Settings()
    for key, spec in SETTINGS_REGISTRY.items():
        assert spec.key == key
        assert spec.title.strip()
        assert spec.description.strip()
        assert spec.category.strip()
        assert describe_value_field(spec).type in {
            "integer",
            "number",
            "boolean",
            "string",
            "enum",
        }
        assert default_value(spec, settings) is not None
