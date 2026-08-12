"""Read-функции для trace/span (T-307).

Запросы только на чтение. Workspace-фильтр обязателен (ADR-3).
User isolation: пользователь видит только свои трассировки, admin — все в workspace.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Span, Trace


async def list_traces(
    session: AsyncSession,
    *,
    workspace_id: str,
    user_id: str,
    is_admin: bool,
    conversation_id: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Trace], int]:
    """Список трассировок. Для admin — все в workspace, иначе — только свои."""
    base = select(Trace).where(Trace.workspace_id == workspace_id)
    count_base = select(func.count()).select_from(Trace).where(Trace.workspace_id == workspace_id)

    if not is_admin:
        base = base.where(Trace.user_id == user_id)
        count_base = count_base.where(Trace.user_id == user_id)

    if conversation_id is not None:
        base = base.where(Trace.conversation_id == conversation_id)
        count_base = count_base.where(Trace.conversation_id == conversation_id)

    base = base.order_by(Trace.ts.desc()).limit(limit).offset(offset)

    traces = (await session.execute(base)).scalars().all()
    total = (await session.execute(count_base)).scalar_one()
    return list(traces), total


async def get_trace(
    session: AsyncSession,
    *,
    workspace_id: str,
    trace_id: str,
    user_id: str,
    is_admin: bool,
) -> Trace | None:
    """Одна трассировка. Для admin — любая в workspace, иначе — только своя."""
    query = select(Trace).where(
        Trace.id == trace_id,
        Trace.workspace_id == workspace_id,
    )
    if not is_admin:
        query = query.where(Trace.user_id == user_id)
    return (await session.execute(query)).scalar_one_or_none()


async def get_spans(
    session: AsyncSession,
    *,
    workspace_id: str,
    trace_id: str,
) -> list[Span]:
    """Все span'ы трассировки, упорядоченные по started_at."""
    query = (
        select(Span)
        .where(
            Span.trace_id == trace_id,
            Span.workspace_id == workspace_id,
        )
        .order_by(Span.started_at)
    )
    return list((await session.execute(query)).scalars().all())
