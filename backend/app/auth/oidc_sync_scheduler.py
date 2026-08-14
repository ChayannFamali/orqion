"""Фоновая синхронизация OIDC-пользователей (T-405).

Периодически refresh-ит refresh_token каждого OIDC-пользователя,
обновляет группы → роли, деактивирует при явном отказе IdP.

asyncio.create_task в lifespan, отменяется при остановке.
Без внешних зависимостей (AGENTS.md §4.2).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.oidc_provider import OidcIdentityProvider
from app.config import Settings
from app.db.models import User

logger = logging.getLogger(__name__)

# Сетевые ошибки IdP — пропускаем, повторим в следующем цикле.
# HTTPStatusError для 5xx тоже трактуем как сетевую (sync_user ловит 400/401).
_NETWORK_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.HTTPStatusError,
)


async def oidc_sync_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    secret_key: str,
    workspace_id: str,
) -> None:
    """Периодически синхронизирует группы OIDC-пользователей.

    Цикл: sleep → refresh всех OIDC-пользователей с refresh_token → repeat.
    Отменяется через asyncio.CancelledError.

    Сетевые ошибки IdP (5xx, timeout, connect) — пропускаются, логируются.
    Явный отказ IdP (400/401) — пользователь деактивируется (sync_user).
    """
    if not settings.oidc_sync_enabled:
        return

    interval = settings.oidc_sync_interval_seconds

    while True:
        await asyncio.sleep(interval)

        try:
            async with session_factory() as session:
                result = await session.execute(
                    select(User).where(
                        User.workspace_id == workspace_id,
                        User.is_active.is_(True),
                        User.refresh_token_enc.is_not(None),
                    )
                )
                users = result.scalars().all()

                if not users:
                    continue

                provider = OidcIdentityProvider(
                    session=session,
                    settings=settings,
                    workspace_id=workspace_id,
                )
                # secret_key передаётся через provider для шифрования/расшифровки
                provider._secret_key = secret_key

                synced = 0
                deactivated = 0
                for user in users:
                    try:
                        ok = await provider.sync_user(user)
                        if ok:
                            synced += 1
                        else:
                            deactivated += 1
                    except _NETWORK_ERRORS:
                        logger.warning(
                            "oidc_sync_network_error",
                            extra={"user_id": user.id, "email": user.email},
                        )

                await session.commit()

                if synced or deactivated:
                    logger.info(
                        "oidc_sync_completed",
                        extra={
                            "synced": synced,
                            "deactivated": deactivated,
                            "total": len(users),
                        },
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("oidc_sync_scheduler_error")
