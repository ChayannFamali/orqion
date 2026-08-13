"""Pydantic-схемы для users API (T-311)."""

from __future__ import annotations

from pydantic import BaseModel


class UserListItem(BaseModel):
    """Пользователь в списке."""

    id: str
    email: str
    is_active: bool
    role_id: str
    role_name: str
    is_builtin_role: bool


class UserListResponse(BaseModel):
    """Список пользователей."""

    users: list[UserListItem]


class UserDetailResponse(BaseModel):
    """Детали пользователя."""

    id: str
    email: str
    is_active: bool
    role_id: str
    role_name: str
    is_builtin_role: bool


class UserUpdate(BaseModel):
    """Обновление пользователя. role_id и/или is_active."""

    role_id: str | None = None
    is_active: bool | None = None
