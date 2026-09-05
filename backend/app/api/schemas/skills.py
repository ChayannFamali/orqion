"""Т-508: схемы скиллов — пакетов конфигурации агентного прогона.

Декларативный скилл (решение 1 мини-дизайн-ревью): фрагмент системного
промпта, подмножество инструментов единого реестра и дефолт
``max_tokens``. Исполняемого содержимого нет.

Две защиты построением, а не проверкой:

- ``extra="forbid"`` — неподписанное поле (``auto_approve``,
  ``trusted_tools``) даёт 422, а не молчаливое игнорирование: схема
  физически не допускает признака «доверенности» инструмента, поэтому
  цикл подтверждения деструктивных действий обойти нельзя (решение 3);
- ``tools`` по умолчанию пуст — пустой список означает НОЛЬ
  инструментов, а не «не сужать» (решение 6, правка пользователя).

Имена инструментов при сохранении проверяются только по формату:
внешние имена (``<сервер>.<инструмент>``) существуют лишь в момент
обнаружения, сервер может быть зарегистрирован позже скилла, а при
К2/К3 их нет вовсе — жёсткая проверка существования сделала бы скилл
непригодным до регистрации сервера. Расходование происходит деградацией
в прогоне.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

NAME_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 512
TOOL_NAME_MAX_LENGTH = 128


def _validate_tool_name(value: str) -> str:
    """Формат имени инструмента: непустое, без пробелов и управляющих символов.

    Состав реестра здесь не проверяется намеренно — см. докстринг модуля.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("Имя инструмента не может быть пустым")
    if any(char.isspace() for char in stripped):
        raise ValueError("Имя инструмента не может содержать пробелы")
    if any(ord(char) < 32 for char in stripped):
        raise ValueError("Имя инструмента не может содержать управляющие символы")
    if len(stripped) > TOOL_NAME_MAX_LENGTH:
        raise ValueError(f"Имя инструмента: не более {TOOL_NAME_MAX_LENGTH} символов")
    return stripped


def _validate_tools(value: list[str]) -> list[str]:
    """Формат каждого имени + дедупликация с сохранением порядка."""
    cleaned: list[str] = []
    for item in value:
        name = _validate_tool_name(item)
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    description: str = Field(default="", max_length=DESCRIPTION_MAX_LENGTH)
    prompt_text: str = ""
    tools: list[str] = Field(default_factory=list)
    default_max_tokens: int | None = Field(default=None, ge=1)
    enabled: bool = True

    _tools = field_validator("tools")(_validate_tools)


class SkillUpdate(BaseModel):
    """Полная замена полей скилла (по образцу шаблонов промптов, Т-507).

    Имя в отличие от сервера протокола переименовывать можно: оно
    отображаемое, а ссылкой служит ``skill_id``, поэтому переименование
    не меняет ни имён инструментов в реестре, ни записей аудита.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    description: str = Field(default="", max_length=DESCRIPTION_MAX_LENGTH)
    prompt_text: str = ""
    tools: list[str] = Field(default_factory=list)
    default_max_tokens: int | None = Field(default=None, ge=1)
    enabled: bool = True

    _tools = field_validator("tools")(_validate_tools)


class SkillResponse(BaseModel):
    """Админский каталог: полные поля, включая выключенные скиллы."""

    id: str
    name: str
    description: str
    prompt_text: str
    tools: list[str]
    default_max_tokens: int | None
    enabled: bool
    created_at: datetime


class SkillListResponse(BaseModel):
    skills: list[SkillResponse]


class SkillAvailableResponse(BaseModel):
    """Список для выбора в диалоге: только то, что нужно для выбора."""

    id: str
    name: str
    description: str


class SkillAvailableListResponse(BaseModel):
    skills: list[SkillAvailableResponse]


class SkillDeleteResponse(BaseModel):
    deleted: bool
