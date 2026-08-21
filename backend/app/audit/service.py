"""Сервис аудита: запись и чтение. Запись — append-only."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def write_audit(
    session: AsyncSession,
    workspace_id: str,
    actor_user_id: str,
    action: str,
    object_type: str,
    object_id: str | None = None,
    meta: dict[str, object] | None = None,
) -> AuditLog:
    """Записывает событие в audit_log. Append-only — возврата нет."""
    record = AuditLog(
        workspace_id=workspace_id,
        ts=datetime.now(UTC),
        actor_user_id=actor_user_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        meta=meta or {},
    )
    session.add(record)
    await session.flush()
    return record


async def list_audit(
    session: AsyncSession,
    workspace_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
    actor_user_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[AuditLog], int]:
    """Возвращает записи аудита по убыванию времени и общее количество.

    Фильтры: action, actor_user_id, start_date/end_date (ISO date strings).
    """
    base = select(AuditLog).where(AuditLog.workspace_id == workspace_id)

    if action is not None:
        base = base.where(AuditLog.action == action)
    if actor_user_id is not None:
        base = base.where(AuditLog.actor_user_id == actor_user_id)
    if start_date is not None:
        base = base.where(AuditLog.ts >= start_date)
    if end_date is not None:
        base = base.where(AuditLog.ts <= end_date)

    count_result = await session.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    result = await session.execute(base.order_by(AuditLog.ts.desc()).limit(limit).offset(offset))
    return list(result.scalars().all()), total


async def list_audit_export(
    session: AsyncSession,
    workspace_id: str,
    *,
    limit: int = 10_000,
    offset: int = 0,
    action: str | None = None,
    actor_user_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[AuditLog], int]:
    """Возвращает записи аудита по возрастанию времени (ts ASC, id ASC).

    Стабильный порядок для SIEM-пагинации через offset: audit_log append-only,
    ts монотонно растёт — новые записи попадают после уже отданных страниц.
    """
    base = select(AuditLog).where(AuditLog.workspace_id == workspace_id)

    if action is not None:
        base = base.where(AuditLog.action == action)
    if actor_user_id is not None:
        base = base.where(AuditLog.actor_user_id == actor_user_id)
    if start_date is not None:
        base = base.where(AuditLog.ts >= start_date)
    if end_date is not None:
        base = base.where(AuditLog.ts <= end_date)

    count_result = await session.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()

    result = await session.execute(
        base.order_by(AuditLog.ts.asc(), AuditLog.id.asc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all()), total


async def list_distinct_actions(
    session: AsyncSession,
    workspace_id: str,
) -> list[str]:
    """Возвращает все различные значения action в workspace."""
    result = await session.execute(
        select(AuditLog.action)
        .where(AuditLog.workspace_id == workspace_id)
        .distinct()
        .order_by(AuditLog.action)
    )
    return list(result.scalars().all())
