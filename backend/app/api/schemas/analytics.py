"""Схемы ответа для API аналитики."""

from __future__ import annotations

from pydantic import BaseModel


class AnalyticsSummary(BaseModel):
    """Сводка за период."""

    total_requests: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost: float
    total_errors: int
    avg_latency_ms: int | None


class DailyBreakdown(BaseModel):
    """Разбивка по дням."""

    date: str
    requests: int
    tokens_in: int
    tokens_out: int
    cost: float
    errors: int
    avg_latency_ms: int | None


class ModelBreakdown(BaseModel):
    """Разбивка по моделям."""

    model_id: str | None
    model_alias: str | None
    requests: int
    tokens_in: int
    tokens_out: int
    cost: float
    errors: int


class UserBreakdown(BaseModel):
    """Разбивка по пользователям с текущей ролью."""

    user_id: str | None
    user_email: str | None
    role_name: str | None
    requests: int
    tokens_in: int
    tokens_out: int
    cost: float
    errors: int


class AnalyticsResponse(BaseModel):
    """Полный ответ аналитики."""

    summary: AnalyticsSummary
    by_day: list[DailyBreakdown]
    by_model: list[ModelBreakdown]
    by_user: list[UserBreakdown]
