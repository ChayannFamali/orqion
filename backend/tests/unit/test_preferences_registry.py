"""Реестр описаний личных настроек пользователя (Т-512).

Проверяется то, что нельзя увидеть в интерфейсе до отказа: спека
отвергается при создании, а не в момент запроса, поэтому опечатка в
описании ключа (нет дефолта, подписи вариантов не покрывают перечисление)
ловится здесь. Отдельно зафиксирован состав прод-реестра и то, что
человекочитаемые подписи не подменяются машинными значениями.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal

import pytest
from app.preferences.registry import (
    CHAT_SEND_KEY,
    PREFERENCES_REGISTRY,
    SEND_ON_ENTER,
    SEND_ON_SHIFT_ENTER,
    PreferenceSpec,
    default_value,
    get_spec,
    ordered_specs,
)
from pydantic import BaseModel, Field, ValidationError

# ---------------------------------------------------------------------------
# Модели значений
# ---------------------------------------------------------------------------


class DefaultValue(BaseModel):
    value: int = 5


class RequiredValue(BaseModel):
    value: int


class TwoFields(BaseModel):
    value: int = 1
    other: int = 2


class SendKeyValue(BaseModel):
    value: Literal["enter", "shift_enter"] = "enter"


class BoundedValue(BaseModel):
    value: int = Field(default=10, ge=1, le=99)


def _pref_spec(**overrides: Any) -> PreferenceSpec:
    """Спека с разрешимым значением: модель по умолчанию имеет дефолт."""
    base: dict[str, Any] = {
        "key": "test_key",
        "title": "Заголовок",
        "description": "Описание",
        "category": "Общие",
        "value_model": DefaultValue,
    }
    base.update(overrides)
    return PreferenceSpec(**base)


@pytest.fixture
def registry() -> Iterator[dict[str, PreferenceSpec]]:
    """Временно очищает реестр: тесты не должны оставлять в нём ключей."""
    saved = dict(PREFERENCES_REGISTRY)
    PREFERENCES_REGISTRY.clear()
    yield PREFERENCES_REGISTRY
    PREFERENCES_REGISTRY.clear()
    PREFERENCES_REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Валидация спеки при создании
# ---------------------------------------------------------------------------


def test_spec_rejects_value_model_without_single_value_field() -> None:
    """Модель значения обязана описывать ровно одно поле value."""
    with pytest.raises(ValidationError) as exc:
        _pref_spec(value_model=TwoFields)
    assert "ровно одно поле" in str(exc.value)


def test_spec_rejects_value_model_without_default() -> None:
    """Без дефолта до первой записи значение неоткуда взять."""
    with pytest.raises(ValidationError) as exc:
        _pref_spec(value_model=RequiredValue)
    assert "до первой записи" in str(exc.value)


def test_spec_rejects_enum_without_option_titles() -> None:
    """Перечисление без подписей показало бы пользователю машинные значения."""
    with pytest.raises(ValidationError) as exc:
        _pref_spec(value_model=SendKeyValue)
    assert "подписи вариантов не заданы" in str(exc.value)
    assert "enter" in str(exc.value)


def test_spec_rejects_option_titles_for_non_enum_value() -> None:
    with pytest.raises(ValidationError) as exc:
        _pref_spec(value_model=DefaultValue, option_titles={"5": "Пять"})
    assert "не перечисление" in str(exc.value)


def test_spec_rejects_option_titles_with_unknown_option() -> None:
    with pytest.raises(ValidationError) as exc:
        _pref_spec(
            value_model=SendKeyValue,
            option_titles={"enter": "Enter", "shift_enter": "Shift+Enter", "ctrl": "Ctrl"},
        )
    assert "несуществующих вариантов" in str(exc.value)
    assert "ctrl" in str(exc.value)


def test_spec_rejects_partial_option_titles() -> None:
    """Частичное покрытие смешало бы в одном списке подписи и сырые значения."""
    with pytest.raises(ValidationError) as exc:
        _pref_spec(value_model=SendKeyValue, option_titles={"enter": "Enter"})
    assert "нет подписи для вариантов" in str(exc.value)
    assert "shift_enter" in str(exc.value)


def test_spec_accepts_enum_with_full_option_titles() -> None:
    spec = _pref_spec(
        value_model=SendKeyValue,
        option_titles={"enter": "Enter", "shift_enter": "Shift+Enter"},
    )
    assert spec.option_titles["shift_enter"] == "Shift+Enter"


def test_spec_is_frozen() -> None:
    """Описание ключа неизменяемо: правка реестра в рантайме — не штатный путь."""
    spec = _pref_spec()
    with pytest.raises(ValidationError):
        spec.title = "Другой заголовок"


# ---------------------------------------------------------------------------
# Значения и порядок
# ---------------------------------------------------------------------------


def test_default_value_takes_model_default() -> None:
    assert default_value(_pref_spec(value_model=DefaultValue)) == 5
    assert default_value(_pref_spec(value_model=BoundedValue)) == 10


def test_default_value_is_json_serializable() -> None:
    """Значение уходит в JSON ответа и в колонку JSON — только JSON-виды."""
    import json

    for spec in PREFERENCES_REGISTRY.values():
        json.dumps(default_value(spec), ensure_ascii=False)


def test_get_spec_returns_none_for_unknown_key() -> None:
    assert get_spec("нет_такого_ключа") is None


def test_ordered_specs_sorted_by_category_then_key(
    registry: dict[str, PreferenceSpec],
) -> None:
    registry["z_key"] = _pref_spec(key="z_key", category="Альфа")
    registry["a_key"] = _pref_spec(key="a_key", category="Альфа")
    registry["m_key"] = _pref_spec(key="m_key", category="Бета")

    assert [spec.key for spec in ordered_specs()] == ["a_key", "z_key", "m_key"]


# ---------------------------------------------------------------------------
# Прод-реестр
# ---------------------------------------------------------------------------


def test_send_key_registered_with_two_options() -> None:
    """Единственный житель реестра v1 — способ отправки сообщения."""
    spec = get_spec(CHAT_SEND_KEY)
    assert spec is not None
    assert spec.title == "Отправка сообщения"
    assert spec.category == "Чат"
    assert spec.description
    assert default_value(spec) == SEND_ON_ENTER


def test_send_key_option_titles_cover_every_value() -> None:
    spec = get_spec(CHAT_SEND_KEY)
    assert spec is not None
    assert set(spec.option_titles) == {SEND_ON_ENTER, SEND_ON_SHIFT_ENTER}


def test_option_titles_are_human_readable_not_machine_values() -> None:
    """Подпись объясняет поведение, а не повторяет машинное значение."""
    spec = get_spec(CHAT_SEND_KEY)
    assert spec is not None
    for value, label in spec.option_titles.items():
        assert label != value
        assert value not in label.split()


@pytest.mark.parametrize("field_name", ["title", "description"])
def test_prod_registry_texts_have_no_internal_references(field_name: str) -> None:
    """В текстах, которые показывает интерфейс, нет внутренней документации."""
    forbidden = ("ADR", "arch.md", "planning.md", "§", "Т-5")
    for spec in PREFERENCES_REGISTRY.values():
        text = getattr(spec, field_name)
        for token in forbidden:
            assert token not in text, f"{spec.key}: {token!r} в {field_name}"
    for spec in PREFERENCES_REGISTRY.values():
        for label in spec.option_titles.values():
            for token in forbidden:
                assert token not in label, f"{spec.key}: {token!r} в подписи варианта"
