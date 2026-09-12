"""Перевод отказа валидации значения настройки в ошибку API.

Общий для обоих реестров настроек — служебных (``app/settings``) и личных
(``app/preferences``): требование к значению и его текст не зависят от того,
чья это настройка, а расхождение двух копий текста означало бы, что одна и
та же ошибка объясняется пользователю по-разному в двух разделах.

Вынесено из маршрута служебных настроек без изменения поведения: форма
ответа (``error``, ``reason``, ``constraint``, ``hint``) и тексты прежние.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.errors import SettingValueInvalid
from app.settings.registry import (
    ValueFieldDescription,
    coerce_value_model,
    describe_value_model,
)

_TYPE_NAMES: dict[str, str] = {
    "integer": "целое число",
    "number": "число",
    "boolean": "значение да или нет",
    "string": "строка",
    "enum": "одно из значений списка",
}


def _number_text(value: float) -> str:
    """Целое без «.0» — границы диапазона читаются как в описании настройки."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _value_hint(field: ValueFieldDescription) -> str:
    """Требование к значению на русском — из описания поля спеки."""
    kind = _TYPE_NAMES.get(field.type, "значение")
    if field.type == "enum" and field.enum_values:
        return f"Ожидается {kind}: {', '.join(field.enum_values)}"
    if field.min is not None and field.max is not None:
        return f"Ожидается {kind} от {_number_text(field.min)} до {_number_text(field.max)}"
    if field.min is not None:
        return f"Ожидается {kind} не меньше {_number_text(field.min)}"
    if field.max is not None:
        return f"Ожидается {kind} не больше {_number_text(field.max)}"
    return f"Ожидается {kind}"


def _validator_messages(exc: ValidationError) -> list[str]:
    """Сообщения собственных валидаторов модели значения.

    Берутся только они: остальные ошибки pydantic описаны по-английски, а
    требование к значению пользователь читает на языке интерфейса. Свои
    валидаторы пишутся сразу по-русски, поэтому показывается их текст.
    """
    return [
        str(error["msg"]).removeprefix("Value error, ")
        for error in exc.errors()
        if error.get("type") == "value_error"
    ]


def validate_value(key: str, value_model: type[BaseModel], raw: Any) -> Any:
    """Проверяет значение моделью; отказ — 422 с требованием в ``hint``."""
    try:
        return coerce_value_model(value_model, raw)
    except ValidationError as exc:
        field = describe_value_model(value_model)
        messages = _validator_messages(exc)
        raise SettingValueInvalid(
            constraint={
                "key": key,
                "expected": field.type,
                "min": field.min,
                "max": field.max,
                "enum_values": field.enum_values,
            },
            hint="; ".join(messages) if messages else _value_hint(field),
        ) from exc
