"""Тесты суточной агрегации: идемпотентность, совпадение с суммой, multiple models/users.

Проверки:
- aggregate_day заполняет usage_daily
- повторный запуск не удваивает (идемпотентность)
- агрегаты совпадают с суммой сырых событий
- multiple users + multiple models → отдельные строки
- errors считаются правильно
- avg_latency_ms считается
- пустой день → 0 строк, не ошибка
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.db.models import UsageDaily, UsageEvent, User
from app.db.workspace import ensure_default_workspace
from app.usage.aggregate import aggregate_day
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_usage_event(
    session: AsyncSession,
    workspace_id: str,
    user_id: str | None,
    model_id: str | None,
    ts: datetime,
    tokens_in: int = 100,
    tokens_out: int = 50,
    cost: float | None = None,
    latency_ms: int | None = 100,
    status: str = "ok",
) -> None:
    event = UsageEvent(
        workspace_id=workspace_id,
        user_id=user_id,
        model_id=model_id,
        ts=ts,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
        latency_ms=latency_ms,
        status=status,
    )
    session.add(event)


async def _get_daily(
    session: AsyncSession,
    workspace_id: str,
    day: str,
) -> list[UsageDaily]:
    result = await session.execute(
        select(UsageDaily).where(
            UsageDaily.workspace_id == workspace_id,
            UsageDaily.date == day,
        )
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_aggregate_day_fills_usage_daily(
    db_session: AsyncSession,
) -> None:
    """aggregate_day заполняет usage_daily."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day = datetime(2026, 8, 9, tzinfo=UTC)
    await _seed_usage_event(db_session, ws_id, None, None, day, tokens_in=100, tokens_out=50)
    await db_session.flush()

    count = await aggregate_day(db_session, ws_id, day.date())
    assert count == 1

    daily = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily) == 1
    assert daily[0].requests == 1
    assert daily[0].tokens_in == 100
    assert daily[0].tokens_out == 50


@pytest.mark.asyncio
async def test_aggregate_day_idempotent(
    db_session: AsyncSession,
) -> None:
    """Повторный запуск не удваивает значения."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day = datetime(2026, 8, 9, tzinfo=UTC)
    await _seed_usage_event(db_session, ws_id, None, None, day, tokens_in=100, tokens_out=50)
    await db_session.flush()

    # Первый запуск
    await aggregate_day(db_session, ws_id, day.date())
    daily1 = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily1) == 1
    assert daily1[0].requests == 1

    # Добавляем ещё событие
    await _seed_usage_event(db_session, ws_id, None, None, day, tokens_in=200, tokens_out=100)
    await db_session.flush()

    # Повторный запуск — должен заменить, не удвоить
    count = await aggregate_day(db_session, ws_id, day.date())
    assert count == 1
    daily2 = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily2) == 1
    assert daily2[0].requests == 2  # 1 + 1, не 1 + 1 + 1
    assert daily2[0].tokens_in == 300  # 100 + 200, не 100 + 100 + 200


@pytest.mark.asyncio
async def test_aggregate_matches_sum_of_events(
    db_session: AsyncSession,
) -> None:
    """Агрегаты совпадают с суммой сырых событий."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    for i in range(5):
        await _seed_usage_event(
            db_session,
            ws_id,
            None,
            None,
            day,
            tokens_in=100 + i * 10,
            tokens_out=50 + i * 5,
            cost=0.001 * (i + 1),
            latency_ms=100 + i * 50,
        )
    await db_session.flush()

    await aggregate_day(db_session, ws_id, day.date())

    daily = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily) == 1
    row = daily[0]
    assert row.requests == 5
    assert row.tokens_in == 100 + 110 + 120 + 130 + 140  # 600
    assert row.tokens_out == 50 + 55 + 60 + 65 + 70  # 300
    assert abs(row.cost - (0.001 + 0.002 + 0.003 + 0.004 + 0.005)) < 0.0001
    assert row.avg_latency_ms == 200  # avg(100,150,200,250,300)


@pytest.mark.asyncio
async def test_aggregate_separates_users_and_models(
    db_session: AsyncSession,
) -> None:
    """Multiple users + multiple models → отдельные строки."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    from app.auth.passwords import hash_password
    from app.db.models import Role
    from app.policy.presets import BUILTIN_ROLES

    role = Role(
        workspace_id=ws_id,
        name="developer",
        is_builtin=True,
        policy=BUILTIN_ROLES["developer"].model_dump(),
    )
    db_session.add(role)
    await db_session.flush()

    user1 = User(
        workspace_id=ws_id, email="u1@o.l", password_hash=hash_password("p"), role_id=role.id
    )
    user2 = User(
        workspace_id=ws_id, email="u2@o.l", password_hash=hash_password("p"), role_id=role.id
    )
    db_session.add_all([user1, user2])
    await db_session.flush()

    from app.db.models import Model, Provider

    provider = Provider(
        workspace_id=ws_id, kind="openai", base_url="http://x", enabled=True, capabilities={}
    )
    db_session.add(provider)
    await db_session.flush()

    model1 = Model(
        workspace_id=ws_id,
        provider_id=provider.id,
        alias="m1",
        upstream_name="m1",
        locality="local",
    )
    model2 = Model(
        workspace_id=ws_id,
        provider_id=provider.id,
        alias="m2",
        upstream_name="m2",
        locality="local",
    )
    db_session.add_all([model1, model2])
    await db_session.flush()

    day = datetime(2026, 8, 9, tzinfo=UTC)
    # 4 события: 2 пользователя × 2 модели
    await _seed_usage_event(db_session, ws_id, user1.id, model1.id, day, tokens_in=100)
    await _seed_usage_event(db_session, ws_id, user1.id, model2.id, day, tokens_in=200)
    await _seed_usage_event(db_session, ws_id, user2.id, model1.id, day, tokens_in=300)
    await _seed_usage_event(db_session, ws_id, user2.id, model2.id, day, tokens_in=400)
    await db_session.flush()

    count = await aggregate_day(db_session, ws_id, day.date())
    assert count == 4  # 2 users × 2 models

    daily = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily) == 4
    # Проверяем уникальные комбинации
    combos = {(d.user_id, d.model_id) for d in daily}
    assert len(combos) == 4


@pytest.mark.asyncio
async def test_aggregate_counts_errors(
    db_session: AsyncSession,
) -> None:
    """errors считается правильно — только status='error'."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day = datetime(2026, 8, 9, tzinfo=UTC)
    await _seed_usage_event(db_session, ws_id, None, None, day, status="ok")
    await _seed_usage_event(db_session, ws_id, None, None, day, status="error")
    await _seed_usage_event(db_session, ws_id, None, None, day, status="error")
    await _seed_usage_event(db_session, ws_id, None, None, day, status="ok")
    await db_session.flush()

    await aggregate_day(db_session, ws_id, day.date())

    daily = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily) == 1
    assert daily[0].requests == 4
    assert daily[0].errors == 2


@pytest.mark.asyncio
async def test_aggregate_empty_day(
    db_session: AsyncSession,
) -> None:
    """Пустой день → 0 строк, не ошибка."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day = datetime(2026, 8, 9, tzinfo=UTC)
    count = await aggregate_day(db_session, ws_id, day.date())
    assert count == 0

    daily = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily) == 0


@pytest.mark.asyncio
async def test_aggregate_only_same_day_events(
    db_session: AsyncSession,
) -> None:
    """События из других дней не попадают в агрегат."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day1 = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    day2 = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)

    await _seed_usage_event(db_session, ws_id, None, None, day1, tokens_in=100)
    await _seed_usage_event(db_session, ws_id, None, None, day2, tokens_in=200)
    await db_session.flush()

    await aggregate_day(db_session, ws_id, day1.date())

    daily = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily) == 1
    assert daily[0].tokens_in == 100  # только day1

    daily2 = await _get_daily(db_session, ws_id, "2026-08-10")
    assert len(daily2) == 0


@pytest.mark.asyncio
async def test_catch_up_no_aggregates_no_events(
    db_session: AsyncSession,
) -> None:
    """Catch-up: нет агрегатов, нет событий → 0 дней."""
    from app.usage.aggregate import catch_up_missing_days

    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    count = await catch_up_missing_days(db_session, ws_id)
    assert count == 0


@pytest.mark.asyncio
async def test_catch_up_no_aggregates_with_events(
    db_session: AsyncSession,
) -> None:
    """Catch-up: нет агрегатов, есть события за 3 дня → 3 дня досчитаны."""
    from app.usage.aggregate import catch_up_missing_days

    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day1 = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    day2 = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    day3 = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    await _seed_usage_event(db_session, ws_id, None, None, day1, tokens_in=100)
    await _seed_usage_event(db_session, ws_id, None, None, day2, tokens_in=200)
    await _seed_usage_event(db_session, ws_id, None, None, day3, tokens_in=300)
    await db_session.flush()

    # Catch-up: досчитает до 2026-08-09 (вчера относительно 2026-08-10)
    from datetime import date as date_cls

    count = await catch_up_missing_days(db_session, ws_id, today=date_cls(2026, 8, 10))

    assert count == 3

    d1 = await _get_daily(db_session, ws_id, "2026-08-07")
    d2 = await _get_daily(db_session, ws_id, "2026-08-08")
    d3 = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(d1) == 1 and d1[0].tokens_in == 100
    assert len(d2) == 1 and d2[0].tokens_in == 200
    assert len(d3) == 1 and d3[0].tokens_in == 300


@pytest.mark.asyncio
async def test_catch_up_partial_aggregates(
    db_session: AsyncSession,
) -> None:
    """Catch-up: агрегат за день 1, пропущен день 2 → досчитан только день 2."""
    from app.usage.aggregate import aggregate_day, catch_up_missing_days

    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day1 = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    day2 = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)

    await _seed_usage_event(db_session, ws_id, None, None, day1, tokens_in=100)
    await _seed_usage_event(db_session, ws_id, None, None, day2, tokens_in=200)
    await db_session.flush()

    # Агрегируем день 1 вручную
    await aggregate_day(db_session, ws_id, day1.date())

    # Catch-up должен досчитать только день 2 (вчера относительно 2026-08-09)
    from datetime import date as date_cls

    count = await catch_up_missing_days(db_session, ws_id, today=date_cls(2026, 8, 9))

    assert count == 1  # только день 2

    d2 = await _get_daily(db_session, ws_id, "2026-08-08")
    assert len(d2) == 1
    assert d2[0].tokens_in == 200


@pytest.mark.asyncio
async def test_catch_up_all_current(
    db_session: AsyncSession,
) -> None:
    """Catch-up: агрегаты актуальны → 0 дней."""
    from app.usage.aggregate import aggregate_day, catch_up_missing_days

    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    await _seed_usage_event(db_session, ws_id, None, None, day, tokens_in=100)
    await db_session.flush()
    await aggregate_day(db_session, ws_id, day.date())

    from datetime import date as date_cls

    count = await catch_up_missing_days(db_session, ws_id, today=date_cls(2026, 8, 10))

    assert count == 0  # последний агрегат = 2026-08-09, вчера = 2026-08-09


@pytest.mark.asyncio
async def test_aggregate_day_null_user_model_uses_sentinel(
    db_session: AsyncSession,
) -> None:
    """BUG-008: events с user_id=None/model_id=None → sentinel UUID в usage_daily.

    PostgreSQL PK implicit NOT NULL — NULL в PK columns недопустим.
    aggregate_day подставляет NIL_ID вместо None.
    """
    from app.usage.constants import NIL_ID

    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    day = datetime(2026, 8, 9, tzinfo=UTC)
    # Событие без user_id и model_id (probe request, error до выбора модели)
    await _seed_usage_event(db_session, ws_id, None, None, day, tokens_in=100)
    await db_session.flush()

    count = await aggregate_day(db_session, ws_id, day.date())
    assert count == 1

    daily = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily) == 1
    assert daily[0].user_id == NIL_ID
    assert daily[0].model_id == NIL_ID
    assert daily[0].requests == 1
    assert daily[0].tokens_in == 100


@pytest.mark.asyncio
async def test_aggregate_day_mixed_null_and_real_ids(
    db_session: AsyncSession,
) -> None:
    """BUG-008: mixed events — real user/model и anonymous → separate rows.

    Sentinel-строка и real-строка не схлопываются (different PK).
    """
    from app.usage.constants import NIL_ID

    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    from app.auth.passwords import hash_password
    from app.db.models import Model, Provider, Role, User
    from app.policy.presets import BUILTIN_ROLES

    role = Role(
        workspace_id=ws_id,
        name="developer",
        is_builtin=True,
        policy=BUILTIN_ROLES["developer"].model_dump(),
    )
    db_session.add(role)
    await db_session.flush()

    user = User(
        workspace_id=ws_id, email="u@o.l", password_hash=hash_password("p"), role_id=role.id
    )
    db_session.add(user)
    await db_session.flush()

    provider = Provider(
        workspace_id=ws_id, kind="openai", base_url="http://x", enabled=True, capabilities={}
    )
    db_session.add(provider)
    await db_session.flush()

    model = Model(
        workspace_id=ws_id,
        provider_id=provider.id,
        alias="m1",
        upstream_name="m1",
        locality="local",
    )
    db_session.add(model)
    await db_session.flush()

    day = datetime(2026, 8, 9, tzinfo=UTC)
    # Anonymous event (no user, no model)
    await _seed_usage_event(db_session, ws_id, None, None, day, tokens_in=50)
    # Real user + model event
    await _seed_usage_event(db_session, ws_id, user.id, model.id, day, tokens_in=100)
    await db_session.flush()

    count = await aggregate_day(db_session, ws_id, day.date())
    assert count == 2  # 2 группы: (NIL, NIL) и (user.id, model.id)

    daily = await _get_daily(db_session, ws_id, "2026-08-09")
    assert len(daily) == 2
    combos = {(d.user_id, d.model_id) for d in daily}
    assert (NIL_ID, NIL_ID) in combos
    assert (user.id, model.id) in combos
