"""GET /api/traces — read API для панели трассировки (T-307).

Доступ: capability "view_traces". Workspace-фильтр обязателен (ADR-3).
User isolation: пользователь видит только свои трассировки, admin — все в workspace.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.trace import (
    SpanResponse,
    TraceDetailResponse,
    TraceListResponse,
    TraceSummaryResponse,
)
from app.auth.dependencies import current_user
from app.db.models import User
from app.db.session import get_session
from app.errors import NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy
from app.trace.queries import get_spans, get_trace, list_traces

router = APIRouter(
    prefix="/api/traces",
    tags=["traces"],
    dependencies=[Depends(current_user)],
)


def _has_view_traces(capabilities: list[str]) -> bool:
    return WILDCARD in capabilities or "view_traces" in capabilities


@router.get("", response_model=TraceListResponse)
async def list_traces_endpoint(
    request: Request,
    conversation_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> TraceListResponse:
    """Список трассировок. Для admin — все в workspace, иначе — только свои."""
    workspace_id = request.app.state.workspace_id
    policy = await resolve_policy(session, user)
    if not _has_view_traces(policy.capabilities):
        raise NotFound(
            constraint={"object": "traces", "reason": "view_traces required"},
            hint="Нет права на просмотр трассировок",
        )

    is_admin = WILDCARD in policy.capabilities
    traces, total = await list_traces(
        session,
        workspace_id=workspace_id,
        user_id=user.id,
        is_admin=is_admin,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
    )

    # Считаем span_count для каждой трассировки
    from sqlalchemy import func, select

    from app.db.models import Span

    span_counts: dict[str, int] = {}
    if traces:
        trace_ids = [t.id for t in traces]
        count_query = (
            select(Span.trace_id, func.count())
            .where(Span.trace_id.in_(trace_ids))
            .group_by(Span.trace_id)
        )
        result = await session.execute(count_query)
        span_counts = {row[0]: row[1] for row in result.all()}

    return TraceListResponse(
        traces=[
            TraceSummaryResponse(
                id=t.id,
                conversation_id=t.conversation_id,
                message_id=t.message_id,
                ts=t.ts,
                total_ms=t.total_ms,
                status=t.status,
                span_count=span_counts.get(t.id, 0),
            )
            for t in traces
        ],
        total=total,
    )


@router.get("/{trace_id}", response_model=TraceDetailResponse)
async def get_trace_endpoint(
    trace_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> TraceDetailResponse:
    """Полная трассировка со всеми span'ами."""
    workspace_id = request.app.state.workspace_id
    policy = await resolve_policy(session, user)
    if not _has_view_traces(policy.capabilities):
        raise NotFound(
            constraint={"object": "traces", "reason": "view_traces required"},
            hint="Нет права на просмотр трассировок",
        )

    is_admin = WILDCARD in policy.capabilities
    trace = await get_trace(
        session,
        workspace_id=workspace_id,
        trace_id=trace_id,
        user_id=user.id,
        is_admin=is_admin,
    )
    if trace is None:
        raise NotFound(
            constraint={"object": "trace", "id": trace_id},
            hint="Трассировка не найдена",
        )

    spans = await get_spans(
        session,
        workspace_id=workspace_id,
        trace_id=trace_id,
    )

    return TraceDetailResponse(
        id=trace.id,
        conversation_id=trace.conversation_id,
        message_id=trace.message_id,
        ts=trace.ts,
        total_ms=trace.total_ms,
        status=trace.status,
        spans=[
            SpanResponse(
                id=s.id,
                name=s.name,
                started_at=s.started_at,
                duration_ms=s.duration_ms,
                payload=s.payload,
            )
            for s in spans
        ],
    )
