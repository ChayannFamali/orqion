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


async def get_last_aggregated_date(
    session: AsyncSession,
    workspace_id: str,
) -> date | None:
    """Возвращает последнюю агрегированную дату или None, если данных нет."""
    result = await session.execute(
        select(func.max(UsageDaily.date)).where(UsageDaily.workspace_id == workspace_id)
    )
    last_str = result.scalar_one_or_none()
    if last_str is None:
        return None
    return date.fromisoformat(last_str)


async def catch_up_missing_days(
    session: AsyncSession,
    workspace_id: str,
    today: date | None = None,
) -> int:
    """Досчитывает пропущенные дни при старте.

    Профиль minimal: ноутбук не работает 24/7. Если приложение не запускалось
    несколько дней — aggregate_scheduler не отработал. Эта функция находит
    последнюю агрегированную дату и досчитывает все дни до вчера включительно.

    today: по умолчанию datetime.now(UTC).date(). Передаётся явно в тестах.
    Возвращает количество обработанных дней.
    """
    if today is None:
        today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)

    last = await get_last_aggregated_date(session, workspace_id)
    if last is not None:
        start_day = last + timedelta(days=1)
    else:
        # Ни одного агрегата — найдём первую дату в usage_event
        first_result = await session.execute(
            select(func.min(UsageEvent.ts)).where(UsageEvent.workspace_id == workspace_id)
        )
        first_ts = first_result.scalar_one_or_none()
        if first_ts is None:
            return 0  # Нет событий — нечего агрегировать
        start_day = first_ts.date()

    if start_day > yesterday:
        return 0  # Всё актуально

    count = 0
    current = start_day
    while current <= yesterday:
        await aggregate_day(session, workspace_id, current)
        current += timedelta(days=1)
        count += 1

    logger.info("Catch-up: aggregated %d missing days for workspace=%s", count, workspace_id)
    return count
