"""GET /api/analytics — срезы по дням, моделям, ролям, пользователям.

arch.md §5.3: источник — usage_daily. Доступ по праву view_analytics.
Проверка через resolve_policy(user).capabilities (§5.2, не role.name).
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
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
from app.policy.models import WILDCARD, Policy
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(current_user)]
)


class AnalyticsForbidden(OrqionError):
    error_code = "analytics_forbidden"
    reason = "Нет права на просмотр аналитики"
    status_code = 403
    hint = "Требуется право view_analytics"


async def _check_access(session: AsyncSession, user: User) -> Policy:
    """Проверяет view_analytics через capabilities, не role.name (§5.2).

    Возвращает Policy для проверки is_admin (capabilities=["*"]).
    """
    policy = await resolve_policy(session, user)
    if WILDCARD not in policy.capabilities and "view_analytics" not in policy.capabilities:
        raise AnalyticsForbidden()
    return policy


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
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    start: str | None = Query(None, description="Начало периода (ISO date)"),
    end: str | None = Query(None, description="Конец периода (ISO date)"),
    model_limit: int | None = Query(None, description="Лимит строк в by_model", ge=1, le=100),
    model_sort: str = Query(
        "requests", description="Сортировка by_model: requests|cost|tokens|errors"
    ),
    user_limit: int | None = Query(None, description="Лимит строк в by_user", ge=1, le=100),
    user_sort: str = Query(
        "requests", description="Сортировка by_user: requests|cost|tokens|errors"
    ),
) -> AnalyticsResponse:
    """Полный ответ аналитики: summary + by_day + by_model + by_user.

    model_limit/model_sort и user_limit/user_sort — server-side top-N
    и сортировка для by_model/by_user (TD-11).
    """
    await _check_access(session, user)
    workspace_id = request.app.state.workspace_id
    date_range = _parse_range(start, end)

    policy = await resolve_policy(session, user)
    is_admin = WILDCARD in policy.capabilities
    # Admin: team_filter=None → no filter (sees all).
    # Non-admin with team: team_filter=user.team_id → filter by team.
    # Non-admin without team: team_filter="" → matches nothing (sees empty).
    team_filter = None if is_admin else (user.team_id or "")

    summary_dict = await get_summary(session, workspace_id, date_range, team_filter=team_filter)
    by_day_list = await get_by_day(session, workspace_id, date_range, team_filter=team_filter)
    by_model_list = await get_by_model(
        session,
        workspace_id,
        date_range,
        limit=model_limit,
        sort_by=model_sort,
        team_filter=team_filter,
    )
    by_user_list = await get_by_user(
        session,
        workspace_id,
        date_range,
        limit=user_limit,
        sort_by=user_sort,
        team_filter=team_filter,
    )

    return AnalyticsResponse(
        summary=AnalyticsSummary(**summary_dict),
        by_day=[DailyBreakdown(**d) for d in by_day_list],
        by_model=[ModelBreakdown(**m) for m in by_model_list],
        by_user=[UserBreakdown(**u) for u in by_user_list],
    )


@router.get("/export")
async def export_analytics(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    start: str | None = Query(None, description="Начало периода (ISO date)"),
    end: str | None = Query(None, description="Конец периода (ISO date)"),
    model_limit: int | None = Query(None, ge=1, le=100),
    model_sort: str = Query("requests"),
    user_limit: int | None = Query(None, ge=1, le=100),
    user_sort: str = Query("requests"),
) -> Response:
    """Экспорт аналитики в CSV (T-434).

    Паттерн — T-428 (audit export): Content-Disposition: attachment,
    те же фильтры/сортировка, что у GET /api/analytics. Access control:
    view_analytics → 403 (AnalyticsForbidden), не 404 (T-120).
    """
    await _check_access(session, user)
    workspace_id = request.app.state.workspace_id
    date_range = _parse_range(start, end)

    policy = await resolve_policy(session, user)
    is_admin = WILDCARD in policy.capabilities
    team_filter = None if is_admin else (user.team_id or "")

    summary_dict = await get_summary(session, workspace_id, date_range, team_filter=team_filter)
    by_day_list = await get_by_day(session, workspace_id, date_range, team_filter=team_filter)
    by_model_list = await get_by_model(
        session,
        workspace_id,
        date_range,
        limit=model_limit,
        sort_by=model_sort,
        team_filter=team_filter,
    )
    by_user_list = await get_by_user(
        session,
        workspace_id,
        date_range,
        limit=user_limit,
        sort_by=user_sort,
        team_filter=team_filter,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "section",
            "date",
            "model_alias",
            "user_email",
            "role_name",
            "team_name",
            "requests",
            "tokens_in",
            "tokens_out",
            "cost",
            "errors",
            "avg_latency_ms",
        ]
    )

    # Summary row
    writer.writerow(
        [
            "summary",
            "",
            "",
            "",
            "",
            "",
            summary_dict["total_requests"],
            summary_dict["total_tokens_in"],
            summary_dict["total_tokens_out"],
            summary_dict["total_cost"],
            summary_dict["total_errors"],
            summary_dict.get("avg_latency_ms", ""),
        ]
    )

    for d in by_day_list:
        writer.writerow(
            [
                "daily",
                d["date"],
                "",
                "",
                "",
                "",
                d["requests"],
                d["tokens_in"],
                d["tokens_out"],
                d["cost"],
                d["errors"],
                d.get("avg_latency_ms", ""),
            ]
        )

    for m in by_model_list:
        writer.writerow(
            [
                "model",
                "",
                m.get("model_alias", ""),
                "",
                "",
                "",
                m["requests"],
                m["tokens_in"],
                m["tokens_out"],
                m["cost"],
                m["errors"],
                "",
            ]
        )

    for u in by_user_list:
        writer.writerow(
            [
                "user",
                "",
                "",
                u.get("user_email", ""),
                u.get("role_name", ""),
                u.get("team_name", ""),
                u["requests"],
                u["tokens_in"],
                u["tokens_out"],
                u["cost"],
                u["errors"],
                "",
            ]
        )

    content = output.getvalue()
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="analytics.csv"',
            "X-Export-Sections": "summary,daily,model,user",
        },
    )
