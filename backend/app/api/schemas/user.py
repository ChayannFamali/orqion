"""Pydantic-схемы для users API (T-311, TD-10)."""

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
    team_id: str | None = None
    team_name: str | None = None
    must_change_password: bool = False


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
    team_id: str | None = None
    team_name: str | None = None
    must_change_password: bool = False


class UserUpdate(BaseModel):
    """Обновление пользователя. role_id, is_active, team_id."""

    role_id: str | None = None
    is_active: bool | None = None
    team_id: str | None = None


class UserCreateRequest(BaseModel):
    """Создание пользователя (TD-10)."""

    email: str
    role_id: str
    team_id: str | None = None


class UserCreateResponse(BaseModel):
    """Ответ при создании пользователя — password показывается один раз."""

    id: str
    email: str
    is_active: bool
    role_id: str
    role_name: str
    team_id: str | None = None
    must_change_password: bool
    password: str
