"""Суточная агрегация usage_event → usage_daily.

arch.md §5.3, ADR-16: идемпотентный пересчёт за день.
Повторный запуск не удваивает — удаляет старые строки и вставляет новые.
Запускается ночью (lifespan scheduler) или вручную.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageDaily, UsageEvent

logger = logging.getLogger("orqion.usage.aggregate")


async def aggregate_day(
    session: AsyncSession,
    workspace_id: str,
    day: date,
) -> int:
    """Агрегирует usage_event за указанный день в usage_daily.

    Идемпотентно: удаляет существующие строки за (workspace_id, day),
    затем вставляет новые из сырых событий.

    Возвращает количество агрегированных групп (строк в usage_daily).
    """
    day_str = day.isoformat()
    day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)

    # Удаляем старые агрегаты за этот день
    await session.execute(
        delete(UsageDaily).where(
            UsageDaily.workspace_id == workspace_id,
            UsageDaily.date == day_str,
        )
    )

    # Агрегируем сырые события
    result = await session.execute(
        select(
            UsageEvent.user_id,
            UsageEvent.model_id,
            func.count(UsageEvent.id).label("requests"),
            func.coalesce(func.sum(UsageEvent.tokens_in), 0).label("tokens_in"),
            func.coalesce(func.sum(UsageEvent.tokens_out), 0).label("tokens_out"),
            func.coalesce(func.sum(UsageEvent.cost), 0).label("cost"),
            func.coalesce(
                func.sum(case((UsageEvent.status == "error", 1), else_=0)),
                0,
            ).label("errors"),
            func.avg(UsageEvent.latency_ms).label("avg_latency_ms"),
        )
        .where(
            UsageEvent.workspace_id == workspace_id,
            UsageEvent.ts >= day_start,
            UsageEvent.ts < day_end,
        )
        .group_by(UsageEvent.user_id, UsageEvent.model_id)
    )
    rows = result.all()

    for row in rows:
        avg_lat = row.avg_latency_ms
        daily = UsageDaily(
            workspace_id=workspace_id,
            date=day_str,
            user_id=row.user_id,
            model_id=row.model_id,
            requests=row.requests,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            cost=round(float(row.cost), 6),
            errors=row.errors,
            avg_latency_ms=int(avg_lat) if avg_lat is not None else None,
        )
        session.add(daily)

    await session.commit()
    logger.info(
        "Aggregated %d groups for workspace=%s date=%s",
        len(rows),
        workspace_id,
        day_str,
    )
    return len(rows)


async def aggregate_yesterday(
    session: AsyncSession,
    workspace_id: str,
) -> int:
    """Агрегирует вчерашний день. Вызывается scheduler'ом ночью."""
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    return await aggregate_day(session, workspace_id, yesterday)
