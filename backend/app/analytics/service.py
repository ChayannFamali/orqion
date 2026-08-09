"""Сервис аналитики: запросы к usage_daily с JOIN для разреза по роли.

arch.md §5.3: источник — usage_daily, не сырые события.
Роль подтягивается через JOIN user → role, текущая на момент запроса.

MVP-упрощение: manager видит весь workspace, как admin. Фильтрация по
подразделению/команде не реализована — нет поля team/department на User.
См. T-402a. Когда будет добавлено — фильтр по user.team_id должен быть
на уровне SQL WHERE, не пост-обработкой.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Model, Role, UsageDaily, User


@dataclass(frozen=True)
class DateRange:
    start: str  # ISO date "2026-08-01"
    end: str  # ISO date "2026-08-09"


async def get_summary(
    session: AsyncSession,
    workspace_id: str,
    date_range: DateRange,
) -> dict[str, Any]:
    """Сводка за период: totals по всем дням."""
    result = await session.execute(
        select(
            func.coalesce(func.sum(UsageDaily.requests), 0).label("total_requests"),
            func.coalesce(func.sum(UsageDaily.tokens_in), 0).label("total_tokens_in"),
            func.coalesce(func.sum(UsageDaily.tokens_out), 0).label("total_tokens_out"),
            func.coalesce(func.sum(UsageDaily.cost), 0.0).label("total_cost"),
            func.coalesce(func.sum(UsageDaily.errors), 0).label("total_errors"),
            func.avg(UsageDaily.avg_latency_ms).label("avg_latency_ms"),
        ).where(
            UsageDaily.workspace_id == workspace_id,
            UsageDaily.date >= date_range.start,
            UsageDaily.date <= date_range.end,
        )
    )
    row = result.one()
    avg_lat = row.avg_latency_ms
    return {
        "total_requests": int(row.total_requests),
        "total_tokens_in": int(row.total_tokens_in),
        "total_tokens_out": int(row.total_tokens_out),
        "total_cost": round(float(row.total_cost), 6),
        "total_errors": int(row.total_errors),
        "avg_latency_ms": int(avg_lat) if avg_lat is not None else None,
    }


async def get_by_day(
    session: AsyncSession,
    workspace_id: str,
    date_range: DateRange,
) -> list[dict[str, Any]]:
    """Разбивка по дням."""
    result = await session.execute(
        select(
            UsageDaily.date,
            func.sum(UsageDaily.requests).label("requests"),
            func.sum(UsageDaily.tokens_in).label("tokens_in"),
            func.sum(UsageDaily.tokens_out).label("tokens_out"),
            func.sum(UsageDaily.cost).label("cost"),
            func.sum(UsageDaily.errors).label("errors"),
            func.avg(UsageDaily.avg_latency_ms).label("avg_latency_ms"),
        )
        .where(
            UsageDaily.workspace_id == workspace_id,
            UsageDaily.date >= date_range.start,
            UsageDaily.date <= date_range.end,
        )
        .group_by(UsageDaily.date)
        .order_by(UsageDaily.date)
    )
    rows = result.all()
    return [
        {
            "date": row.date,
            "requests": int(row.requests or 0),
            "tokens_in": int(row.tokens_in or 0),
            "tokens_out": int(row.tokens_out or 0),
            "cost": round(float(row.cost or 0), 6),
            "errors": int(row.errors or 0),
            "avg_latency_ms": int(row.avg_latency_ms) if row.avg_latency_ms is not None else None,
        }
        for row in rows
    ]


async def get_by_model(
    session: AsyncSession,
    workspace_id: str,
    date_range: DateRange,
) -> list[dict[str, Any]]:
    """Разбивка по моделям."""
    result = await session.execute(
        select(
            UsageDaily.model_id,
            Model.alias.label("model_alias"),
            func.sum(UsageDaily.requests).label("requests"),
            func.sum(UsageDaily.tokens_in).label("tokens_in"),
            func.sum(UsageDaily.tokens_out).label("tokens_out"),
            func.sum(UsageDaily.cost).label("cost"),
            func.sum(UsageDaily.errors).label("errors"),
        )
        .outerjoin(Model, UsageDaily.model_id == Model.id)
        .where(
            UsageDaily.workspace_id == workspace_id,
            UsageDaily.date >= date_range.start,
            UsageDaily.date <= date_range.end,
        )
        .group_by(UsageDaily.model_id, Model.alias)
        .order_by(func.sum(UsageDaily.requests).desc())
    )
    rows = result.all()
    return [
        {
            "model_id": row.model_id,
            "model_alias": row.model_alias,
            "requests": int(row.requests or 0),
            "tokens_in": int(row.tokens_in or 0),
            "tokens_out": int(row.tokens_out or 0),
            "cost": round(float(row.cost or 0), 6),
            "errors": int(row.errors or 0),
        }
        for row in rows
    ]


async def get_by_user(
    session: AsyncSession,
    workspace_id: str,
    date_range: DateRange,
) -> list[dict[str, Any]]:
    """Разбивка по пользователям с текущей ролью (JOIN user → role)."""
    result = await session.execute(
        select(
            UsageDaily.user_id,
            User.email.label("user_email"),
            Role.name.label("role_name"),
            func.sum(UsageDaily.requests).label("requests"),
            func.sum(UsageDaily.tokens_in).label("tokens_in"),
            func.sum(UsageDaily.tokens_out).label("tokens_out"),
            func.sum(UsageDaily.cost).label("cost"),
            func.sum(UsageDaily.errors).label("errors"),
        )
        .outerjoin(User, UsageDaily.user_id == User.id)
        .outerjoin(Role, User.role_id == Role.id)
        .where(
            UsageDaily.workspace_id == workspace_id,
            UsageDaily.date >= date_range.start,
            UsageDaily.date <= date_range.end,
        )
        .group_by(UsageDaily.user_id, User.email, Role.name)
        .order_by(func.sum(UsageDaily.requests).desc())
    )
    rows = result.all()
    return [
        {
            "user_id": row.user_id,
            "user_email": row.user_email,
            "role_name": row.role_name,
            "requests": int(row.requests or 0),
            "tokens_in": int(row.tokens_in or 0),
            "tokens_out": int(row.tokens_out or 0),
            "cost": round(float(row.cost or 0), 6),
            "errors": int(row.errors or 0),
        }
        for row in rows
    ]
