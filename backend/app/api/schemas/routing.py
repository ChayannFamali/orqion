"""Схемы API для CRUD правил маршрутизации (T-114a)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoutingRuleResponse(BaseModel):
    """Ответ: правило маршрутизации."""

    id: str
    order: int
    is_default: bool
    is_terminal: bool
    when_corpus_class: list[str] | None = None
    when_role: str | None = None
    when_task: str | None = None
    when_model_alias: str | None = None
    to_models: list[str] | None = None
    allow_locality: list[str] | None = None
    fallback_models: list[str] | None = None
    reason: str = ""


class RoutingRuleCreate(BaseModel):
    """Создание правила маршрутизации."""

    order: int = Field(ge=0)
    is_default: bool = False
    is_terminal: bool = False
    when_corpus_class: list[str] | None = None
    when_role: str | None = None
    when_task: str | None = None
    when_model_alias: str | None = None
    to_models: list[str] | None = None
    allow_locality: list[str] | None = None
    fallback_models: list[str] | None = None
    reason: str = ""


class RoutingRuleUpdate(BaseModel):
    """Обновление правила маршрутизации. Все поля optional."""

    order: int | None = Field(default=None, ge=0)
    is_default: bool | None = None
    is_terminal: bool | None = None
    when_corpus_class: list[str] | None = None
    when_role: str | None = None
    when_task: str | None = None
    when_model_alias: str | None = None
    to_models: list[str] | None = None
    allow_locality: list[str] | None = None
    fallback_models: list[str] | None = None
    reason: str | None = None


class RoutingRuleListResponse(BaseModel):
    """Список правил маршрутизации."""

    rules: list[RoutingRuleResponse]
    total: int
