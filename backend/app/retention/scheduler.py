"""Фоновая очистка данных по срокам хранения (T-406).

arch.md §5.3: отдельные сроки для span.payload, usage_event, диалогов.
Retention — плановая гигиена, не административное действие.
Не пишет в audit_log (аналог T-119 aggregation).

asyncio.create_task в lifespan, отменяется при остановке.
Без внешних зависимостей (AGENTS.md §4.2).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Conversation, Message, Span, Trace, UsageEvent

logger = logging.getLogger(__name__)


async def retention_cleanup(
    session: AsyncSession,
    settings: Settings,
    workspace_id: str,
) -> dict[str, int]:
    """Очищает устаревшие данные. Возвращает счётчики удалённых строк.

    Порядок (важен для FK):
    1. Span (нет зависимых таблиц)
    2. Trace ( FK к message/conversation — SET NULL)
    3. UsageEvent (FK к message/conversation — SET NULL)
    4. Conversation (cascade удалит messages; только если message_retention_days > 0)
    """
    now = datetime.now(UTC)
    counts: dict[str, int] = {}

    # 1. Span cleanup (0 = бессрочно, не удалять)
    if settings.span_retention_days > 0:
        span_threshold = now - timedelta(days=settings.span_retention_days)
        result = await session.execute(
            delete(Span).where(
                Span.workspace_id == workspace_id,
                Span.created_at < span_threshold,
            )
        )
        counts["spans"] = getattr(result, "rowcount", 0) or 0
    else:
        counts["spans"] = 0

    # 2. Trace cleanup (0 = бессрочно, не удалять)
    if settings.span_retention_days > 0:
        trace_threshold = now - timedelta(days=settings.span_retention_days)
        result = await session.execute(
            delete(Trace).where(
                Trace.workspace_id == workspace_id,
                Trace.created_at < trace_threshold,
            )
        )
        counts["traces"] = getattr(result, "rowcount", 0) or 0
    else:
        counts["traces"] = 0

    # 3. UsageEvent cleanup (0 = бессрочно, не удалять)
    if settings.usage_event_retention_days > 0:
        usage_threshold = now - timedelta(days=settings.usage_event_retention_days)
        result = await session.execute(
            delete(UsageEvent).where(
                UsageEvent.workspace_id == workspace_id,
                UsageEvent.ts < usage_threshold,
            )
        )
        counts["usage_events"] = getattr(result, "rowcount", 0) or 0
    else:
        counts["usage_events"] = 0

    # 4. Conversation cleanup (только если message_retention_days > 0)
    # Сначала удаляем messages (FK conversation_id NOT NULL, без ondelete),
    # затем conversations. trace/usage_event FK уже SET NULL миграцией 0020.
    if settings.message_retention_days > 0:
        conv_threshold = now - timedelta(days=settings.message_retention_days)

        # Находим ID диалогов для удаления
        conv_result = await session.execute(
            select(Conversation.id).where(
                Conversation.workspace_id == workspace_id,
                Conversation.last_activity_at < conv_threshold,
            )
        )
        conv_ids = [row[0] for row in conv_result.all()]

        if conv_ids:
            # Удаляем messages этих диалогов
            await session.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
            # Удаляем сами диалоги
            result = await session.execute(
                delete(Conversation).where(Conversation.id.in_(conv_ids))
            )
            counts["conversations"] = getattr(result, "rowcount", 0) or 0
        else:
            counts["conversations"] = 0
    else:
        counts["conversations"] = 0

    await session.commit()

    total = sum(counts.values())
    if total > 0:
        logger.info(
            "retention_cleanup_completed",
            extra={"workspace_id": workspace_id, **counts},
        )

    return counts


async def retention_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    workspace_id: str,
) -> None:
    """Периодически очищает устаревшие данные.

    Цикл: sleep → cleanup → repeat.
    Отменяется через asyncio.CancelledError.
    Все retention=0 → no-op (ничего не удаляется).
    """
    interval = settings.retention_cleanup_interval_seconds

    while True:
        await asyncio.sleep(interval)
        try:
            async with session_factory() as session:
                await retention_cleanup(session, settings, workspace_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("retention_scheduler_error")
