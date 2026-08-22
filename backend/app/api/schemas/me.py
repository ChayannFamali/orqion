"""Схемы для self-service эндпоинтов (T-424)."""

from __future__ import annotations

from pydantic import BaseModel


class ModelUsageBreakdown(BaseModel):
    """Расход по конкретной модели за текущий месяц."""

    model_id: str
    requests: int
    tokens_in: int
    tokens_out: int
    cost: float


class MyUsageResponse(BaseModel):
    """Личный расход пользователя в текущем месяце (T-424, T-435).

    tokens_limit / cost_limit: None = unlimited по этому измерению.
    Frontend проверяет limit === null per-field для отображения "Без лимита".
    near_limit: True когда использовано >= budget_near_limit_threshold (T-435).
    При budget=None (unlimited) — всегда False.
    """

    tokens_used: int
    tokens_limit: int | None
    cost_used: float
    cost_limit: int | None
    period: str
    by_model: list[ModelUsageBreakdown]
    near_limit: bool = False
