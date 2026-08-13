"""Pydantic-схемы для roles API (T-310)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RoleResponse(BaseModel):
    """Роль с полной политикой для ответа API."""

    id: str
    name: str
    is_builtin: bool
    policy: dict[str, Any]


class RoleListResponse(BaseModel):
    """Список ролей."""

    roles: list[RoleResponse]


class RoleCreate(BaseModel):
    """Создание кастомной роли. is_builtin всегда False (игнорируется из тела)."""

    name: str = Field(min_length=1, max_length=255)
    policy: dict[str, Any]


class RoleUpdate(BaseModel):
    """Обновление политики роли. Только policy, name не меняется."""

    policy: dict[str, Any]
