"""GET /api/analytics — срезы по дням, моделям, ролям, пользователям.

arch.md §5.3: источник — usage_daily. Доступ по праву view_analytics.
Проверка через resolve_policy(user).capabilities (§5.2, не role.name).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import DateRange, get_by_day, get_by_model, get_by_user, get_summary
from app.api.schemas.analytics import (
    AnalyticsResponse,
    AnalyticsSummary,
    DailyBreakdown,
    ModelBreakdown,
    UserBreakdown,
)
from app.auth.dependencies import current_user
from app.db.models import User
from app.db.session import get_session
from app.errors import OrqionError
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(current_user)]
)


class AnalyticsForbidden(OrqionError):
    error_code = "analytics_forbidden"
    reason = "Нет права на просмотр аналитики"
    status_code = 403
    hint = "Требуется право view_analytics"


async def _check_access(session: AsyncSession, user: User) -> None:
    """Проверяет view_analytics через capabilities, не role.name (§5.2)."""
    policy = await resolve_policy(session, user)
    if WILDCARD not in policy.capabilities and "view_analytics" not in policy.capabilities:
        raise AnalyticsForbidden()


def _parse_range(
    start: str | None,
    end: str | None,
) -> DateRange:
    """Парсит диапазон дат. По умолчанию — последние 7 дней."""
    if start is None or end is None:
        today = datetime.now(UTC).date()
        end_dt = today - timedelta(days=1)
        start_dt = end_dt - timedelta(days=6)
        return DateRange(start=start_dt.isoformat(), end=end_dt.isoformat())
    return DateRange(start=start, end=end)


@router.get("", response_model=AnalyticsResponse)
async def get_analytics(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    start: str | None = Query(None, description="Начало периода (ISO date)"),
    end: str | None = Query(None, description="Конец периода (ISO date)"),
) -> AnalyticsResponse:
    """Полный ответ аналитики: summary + by_day + by_model + by_user."""
    await _check_access(session, user)
    workspace_id = user.workspace_id
    date_range = _parse_range(start, end)

    summary_dict = await get_summary(session, workspace_id, date_range)
    by_day_list = await get_by_day(session, workspace_id, date_range)
    by_model_list = await get_by_model(session, workspace_id, date_range)
    by_user_list = await get_by_user(session, workspace_id, date_range)

    return AnalyticsResponse(
        summary=AnalyticsSummary(**summary_dict),
        by_day=[DailyBreakdown(**d) for d in by_day_list],
        by_model=[ModelBreakdown(**m) for m in by_model_list],
        by_user=[UserBreakdown(**u) for u in by_user_list],
    )
