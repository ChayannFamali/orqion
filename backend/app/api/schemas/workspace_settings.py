"""Pydantic-схемы реестра служебных настроек рабочей области.

Форма ответа — контракт рендера вкладки настроек: по каждому
зарегистрированному ключу отдаётся не только значение, но и всё, что нужно,
чтобы нарисовать поле, не зная самого ключа. Новая настройка поэтому не
требует правок во фронтенде.

``editable`` считается на сервере из способности ``write_capability``
именно этого ключа: клиент не знает правил выдачи прав и не должен их
угадывать.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.settings.registry import UiValueType

#: Предел длины ключа совпадает с колонкой ``workspace_settings.key``.
KEY_MAX_LENGTH = 128

#: Откуда взялось отданное значение: из записи в БД или из дефолта.
ValueSource = Literal["default", "db"]


class WorkspaceSettingResponse(BaseModel):
    """Один ключ реестра с резолвнутым значением и описанием поля."""

    key: str
    title: str
    description: str
    category: str
    type: UiValueType
    enum_values: list[str] | None = None
    value: JsonValue = None
    source: ValueSource
    editable: bool
    min: float | None = None
    max: float | None = None


class WorkspaceSettingListResponse(BaseModel):
    settings: list[WorkspaceSettingResponse]


class WorkspaceSettingUpdate(BaseModel):
    """Запись одного ключа за вызов.

    Батча нет намеренно: при отказе по одному из ключей непонятно, какое
    именно поле его вызвало, а частичное применение группы настроек
    оставило бы рабочую область в промежуточном состоянии.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=KEY_MAX_LENGTH)
    value: JsonValue = None
