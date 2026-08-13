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
    is_impersonating: bool = False
    impersonated_by_email: str | None = None


class LoginResponse(BaseModel):
    user: UserResponse
