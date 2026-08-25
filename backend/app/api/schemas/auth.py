"""Схемы запроса и ответа для auth-эндпоинтов."""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    is_active: bool
    capabilities: list[str]
    # Т-445 (каркас): режим рассуждения из политики роли — фронтенд гейтит
    # им переключатель в чате (off/on = фиксированный режим,
    # optional = переключатель виден).
    reasoning: str = "off"
    is_impersonating: bool = False
    impersonated_by_email: str | None = None
    must_change_password: bool = False


class LoginResponse(BaseModel):
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ChangePasswordResponse(BaseModel):
    status: str
