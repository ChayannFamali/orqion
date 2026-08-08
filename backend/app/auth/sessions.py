"""Сессии: создание, проверка, инвалидация. Серверная, в БД."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Session, User

SESSION_TTL_HOURS = 24 * 7  # 7 дней
COOKIE_NAME = "orqion_session"


async def create_session(
    session: AsyncSession,
    user_id: str,
    workspace_id: str,
) -> str:
    """Создаёт сессию, возвращает ID для cookie."""
    record = Session(
        workspace_id=workspace_id,
        user_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=SESSION_TTL_HOURS),
    )
    session.add(record)
    await session.flush()
    return record.id


async def get_user_by_session(
    session: AsyncSession,
    session_id: str,
) -> User | None:
    """Возвращает активного пользователя по ID сессии или None."""
    session_result = await session.execute(
        select(Session)
        .where(Session.id == session_id)
        .where(Session.expires_at > datetime.now(UTC))
    )
    record = session_result.scalar_one_or_none()
    if record is None:
        return None

    user_result = await session.execute(
        select(User).where(User.id == record.user_id).where(User.is_active.is_(True))
    )
    return user_result.scalar_one_or_none()


async def invalidate_session(
    session: AsyncSession,
    session_id: str,
) -> None:
    """Удаляет сессию из БД."""
    await session.execute(delete(Session).where(Session.id == session_id))
