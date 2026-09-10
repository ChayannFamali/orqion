"""Сессии: создание, проверка, инвалидация. Серверная, в БД."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Session, User
from app.settings.registry import SESSION_TTL_KEY
from app.settings.service import read_setting_as

COOKIE_NAME = "orqion_session"


async def create_session(
    session: AsyncSession,
    user_id: str,
    workspace_id: str,
    settings: Settings,
    impersonated_by: str | None = None,
) -> str:
    """Создаёт сессию, возвращает ID для cookie.

    impersonated_by — ID родительской сессии при имперсонации (None для обычной).

    Срок жизни берётся из служебных настроек рабочей области; пока значения
    там нет, действует ``settings.session_ttl_days``, то есть поведение без
    записи в БД не меняется. На уже выданные сессии настройка не влияет:
    срок зафиксирован в ``expires_at`` в момент выдачи.
    """
    ttl_days = await read_setting_as(
        session, workspace_id, SESSION_TTL_KEY, int, app_settings=settings
    )
    record = Session(
        workspace_id=workspace_id,
        user_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
        impersonated_by=impersonated_by,
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


async def get_session_record(
    session: AsyncSession,
    session_id: str,
) -> Session | None:
    """Возвращает запись сессии по ID (включая impersonated_by). Не проверяет expiry."""
    result = await session.execute(select(Session).where(Session.id == session_id))
    return result.scalar_one_or_none()


async def invalidate_session(
    session: AsyncSession,
    session_id: str,
) -> None:
    """Удаляет сессию из БД."""
    await session.execute(delete(Session).where(Session.id == session_id))
