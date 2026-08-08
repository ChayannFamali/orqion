"""Сервис аудита: запись и чтение. Запись — append-only."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
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
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLog]:
    """Возвращает записи аудита по убыванию времени."""
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.workspace_id == workspace_id)
        .order_by(AuditLog.ts.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())
