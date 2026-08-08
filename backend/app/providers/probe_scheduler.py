"""Плановый ре-probe: периодический вызов probe_provider для всех провайдеров.

asyncio.create_task в lifespan, отменяется при остановке.
Без внешних зависимостей (AGENTS.md §4.2 — не вводить APScheduler).
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db.models import Provider
from app.providers.probe import probe_provider

logger = logging.getLogger(__name__)


async def probe_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    secret_key: str,
    interval_seconds: int,
) -> None:
    """Периодически вызывает probe_provider для всех провайдеров.

    Цикл: sleep → probe всех → repeat. Отменяется через asyncio.CancelledError.
    """
    while True:
        await asyncio.sleep(interval_seconds)

        try:
            async with session_factory() as session:
                result = await session.execute(
                    select(Provider)
                    .where(Provider.enabled.is_(True))
                    .options(selectinload(Provider.models))
                )
                providers = result.scalars().unique().all()

                for provider in providers:
                    models = list(provider.models) if provider.models else []
                    probe_result = await probe_provider(provider, models, secret_key)

                    provider.capabilities = {
                        "available_models": probe_result.available_models,
                        "supports_streaming": probe_result.supports_streaming,
                        "max_parallel": probe_result.max_parallel,
                        "last_probe_at": probe_result.probed_at.isoformat(),
                    }
                    provider.last_probe_at = probe_result.probed_at

                    if probe_result.error:
                        logger.warning(
                            "probe_failed",
                            extra={"provider_id": provider.id, "error": probe_result.error},
                        )
                    else:
                        logger.info(
                            "probe_completed",
                            extra={
                                "provider_id": provider.id,
                                "models_available": len(probe_result.available_models),
                            },
                        )

                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("probe_scheduler_error")
