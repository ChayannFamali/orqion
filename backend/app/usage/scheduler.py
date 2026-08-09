"""Scheduler суточной агрегации. Запускается ночью, отменяется при shutdown.

arch.md §5.3: фоновая задача, наполняющая usage_daily.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.usage.aggregate import aggregate_yesterday, catch_up_missing_days

logger = logging.getLogger("orqion.usage.scheduler")

# Запуск в 03:00 UTC, интервал — 24 часа
_RUN_INTERVAL_SECONDS = 24 * 3600


def _seconds_until_next_run() -> float:
    """Секунды до ближайшего 03:00 UTC."""
    now = datetime.now(UTC)
    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target.replace(day=now.day + 1)
    delta = (target - now).total_seconds()
    return max(delta, 0.0)


_INITIAL_DELAY_SECONDS = _seconds_until_next_run()


async def aggregate_scheduler(
    session_factory: async_sessionmaker,  # type: ignore[type-arg]
    workspace_id: str,
) -> None:
    """Фоновый цикл: агрегирует вчерашний день каждые 24 часа.

    При старте досчитывает пропущенные дни (catch-up), затем входит в цикл
    ожидания до 03:00 UTC. Профиль minimal: ноутбук не работает 24/7.

    Отменяется через CancelledError при shutdown.
    """
    # Catch-up: досчитать пропущенные дни при старте
    try:
        async with session_factory() as session:
            caught = await catch_up_missing_days(session, workspace_id)
            if caught > 0:
                logger.info("Catch-up: %d missing days aggregated", caught)
    except asyncio.CancelledError:
        logger.info("Aggregate scheduler cancelled during catch-up")
        raise
    except Exception:
        logger.warning("Catch-up error", exc_info=True)

    logger.info("Aggregate scheduler started: next run in %.0f seconds", _INITIAL_DELAY_SECONDS)
    await asyncio.sleep(_INITIAL_DELAY_SECONDS)
    while True:
        try:
            async with session_factory() as session:
                count = await aggregate_yesterday(session, workspace_id)
                logger.info("Scheduled aggregation: %d groups", count)
        except asyncio.CancelledError:
            logger.info("Aggregate scheduler cancelled")
            raise
        except Exception:
            logger.warning("Aggregate scheduler error", exc_info=True)
        await asyncio.sleep(_RUN_INTERVAL_SECONDS)
