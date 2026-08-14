"""T-406: Retention — фоновая очистка данных по срокам хранения.

Тестируют:
- cleanup старых span/trace/usage_event
- usage_daily переживает purge usage_event
- conversation retention по last_activity_at (не created_at)
- conversation retention отключён по умолчанию (message_retention_days=0)
- catch_up_missing_days guard: не backfill-ит дни старше retention
- backfill миграции: last_activity_at = created_at для существующих диалогов
- retention no-op когда все retention=0
- workspace_id фильтр в cleanup
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.config import Settings
from app.db.models import Conversation, Message, Span, UsageDaily, UsageEvent, Workspace
from app.retention.scheduler import retention_cleanup
from app.usage.aggregate import aggregate_day, catch_up_missing_days
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent.parent / "alembic.ini"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "db" / "migrations"


def _make_alembic_config(db_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def _make_retention_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "span_retention_days": 30,
        "usage_event_retention_days": 90,
        "message_retention_days": 0,
        "retention_cleanup_interval_seconds": 3600,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# Migration backfill test
# ---------------------------------------------------------------------------


def test_last_activity_at_backfill_after_migration(tmp_path: Path) -> None:
    """Миграция 0020: last_activity_at = created_at для существующих диалогов.

    Паттерн по образцу test_trace_span_insert_after_migration:
    1. Создаём БД через alembic upgrade до 0019 (до миграции 0020)
    2. Засеем conversation через raw SQL (без last_activity_at — колонки нет)
    3. Накатываем 0020 (ADD COLUMN → UPDATE → NOT NULL)
    4. Проверяем: last_activity_at == created_at, не NULL
    """
    from sqlalchemy import text

    db_url = f"sqlite:///{tmp_path}/backfill_test.db"
    config = _make_alembic_config(db_url)

    # Upgrade до 0019 (перед 0020)
    command.upgrade(config, "0019")

    engine = create_engine(db_url)
    with Session(engine) as session:
        # Засеем через raw SQL — ORM-модель уже имеет last_activity_at (NOT NULL),
        # но в схеме 0019 этой колонки нет. ORM insert упал бы.
        ws_id = "00000000-0000-0000-0000-000000000001"
        user_id = "00000000-0000-0000-0000-000000000002"
        role_id = "00000000-0000-0000-0000-000000000003"
        conv_id = "00000000-0000-0000-0000-000000000004"

        session.execute(
            text("INSERT INTO workspace (id, name, created_at) VALUES (:id, 'test-ws', :now)"),
            {"id": ws_id, "now": datetime.now(UTC)},
        )
        session.execute(
            text(
                "INSERT INTO role (id, workspace_id, name, is_builtin, policy, created_at) "
                "VALUES (:id, :ws, 'admin', 0, '{}', :now)"
            ),
            {"id": role_id, "ws": ws_id, "now": datetime.now(UTC)},
        )
        session.execute(
            text(
                "INSERT INTO user (id, workspace_id, email, password_hash, role_id, "
                "is_active, auth_method, created_at) "
                "VALUES (:id, :ws, 'test@orqion.local', 'hash', :role, 1, 'local', :now)"
            ),
            {"id": user_id, "ws": ws_id, "role": role_id, "now": datetime.now(UTC)},
        )
        session.execute(
            text(
                "INSERT INTO conversation (id, workspace_id, user_id, title, archived, created_at) "
                "VALUES (:id, :ws, :user, 'old chat', 0, :now)"
            ),
            {"id": conv_id, "ws": ws_id, "user": user_id, "now": datetime.now(UTC)},
        )
        session.commit()

        # Читаем created_at для последующей проверки
        row = session.execute(
            text("SELECT created_at FROM conversation WHERE id = :id"),
            {"id": conv_id},
        ).one()
        created_at_raw = row[0]

    # Накатываем миграцию 0020 (ADD COLUMN → UPDATE → NOT NULL)
    command.upgrade(config, "head")

    with Session(engine) as session:
        result = session.execute(select(Conversation).where(Conversation.id == conv_id))
        conv = result.scalar_one()
        assert conv.last_activity_at is not None
        # created_at_raw может быть строкой (SQLite) — нормализуем
        if isinstance(created_at_raw, str):
            created_at_dt = datetime.fromisoformat(created_at_raw)
        else:
            created_at_dt = created_at_raw
        assert conv.last_activity_at == created_at_dt

    engine.dispose()


# ---------------------------------------------------------------------------
# Retention cleanup tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_span_cleanup_deletes_old_spans(app_fixture: FastAPI) -> None:
    """Span с старым created_at → удалён."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory
    settings = _make_retention_settings(span_retention_days=30)

    async with factory() as session:
        # Создаём trace + span с старой датой
        from app.db.models import Trace

        old_date = datetime.now(UTC) - timedelta(days=60)
        trace = Trace(
            workspace_id=workspace_id,
            user_id=None,
            ts=old_date,
            status="ok",
            created_at=old_date,
        )
        session.add(trace)
        await session.flush()

        span = Span(
            workspace_id=workspace_id,
            trace_id=trace.id,
            name="old_step",
            started_at=old_date,
            payload={},
            created_at=old_date,
        )
        session.add(span)
        await session.commit()
        span_id = span.id

    async with factory() as session:
        counts = await retention_cleanup(session, settings, workspace_id)
        assert counts["spans"] >= 1

    async with factory() as session:
        result = await session.get(Span, span_id)
        assert result is None


@pytest.mark.asyncio
async def test_trace_cleanup_deletes_old_traces(app_fixture: FastAPI) -> None:
    """Trace с старым created_at → удалён."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory
    settings = _make_retention_settings(span_retention_days=30)

    async with factory() as session:
        from app.db.models import Trace

        old_date = datetime.now(UTC) - timedelta(days=60)
        trace = Trace(
            workspace_id=workspace_id,
            user_id=None,
            ts=old_date,
            status="ok",
            created_at=old_date,
        )
        session.add(trace)
        await session.commit()
        trace_id = trace.id

    async with factory() as session:
        counts = await retention_cleanup(session, settings, workspace_id)
        assert counts["traces"] >= 1

    async with factory() as session:
        from app.db.models import Trace

        result = await session.get(Trace, trace_id)
        assert result is None


@pytest.mark.asyncio
async def test_usage_event_cleanup_deletes_old_events(app_fixture: FastAPI) -> None:
    """UsageEvent с старым ts → удалён."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory
    settings = _make_retention_settings(usage_event_retention_days=90)

    async with factory() as session:
        old_date = datetime.now(UTC) - timedelta(days=120)
        event = UsageEvent(
            workspace_id=workspace_id,
            user_id=None,
            model_id=None,
            conversation_id=None,
            message_id=None,
            ts=old_date,
            status="ok",
        )
        session.add(event)
        await session.commit()
        event_id = event.id

    async with factory() as session:
        counts = await retention_cleanup(session, settings, workspace_id)
        assert counts["usage_events"] >= 1

    async with factory() as session:
        result = await session.get(UsageEvent, event_id)
        assert result is None


@pytest.mark.asyncio
async def test_usage_daily_survives_event_purge(app_fixture: FastAPI) -> None:
    """UsageDaily для дня сохраняется после purge usage_event."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory
    settings = _make_retention_settings(usage_event_retention_days=90)

    # Создаём usage_event 100 дней назад
    old_date = datetime.now(UTC) - timedelta(days=100)
    async with factory() as session:
        event = UsageEvent(
            workspace_id=workspace_id,
            user_id=None,
            model_id=None,
            conversation_id=None,
            message_id=None,
            ts=old_date,
            status="ok",
            tokens_in=100,
            tokens_out=50,
        )
        session.add(event)
        await session.commit()

    # Агрегируем этот день
    day = old_date.date()
    async with factory() as session:
        await aggregate_day(session, workspace_id, day)

    # Проверяем: агрегат существует
    async with factory() as session:
        result = await session.execute(
            select(UsageDaily).where(
                UsageDaily.workspace_id == workspace_id,
                UsageDaily.date == day.isoformat(),
            )
        )
        assert result.scalar_one_or_none() is not None

    # Purge через retention
    async with factory() as session:
        await retention_cleanup(session, settings, workspace_id)

    # Проверяем: агрегат всё ещё существует
    async with factory() as session:
        result = await session.execute(
            select(UsageDaily).where(
                UsageDaily.workspace_id == workspace_id,
                UsageDaily.date == day.isoformat(),
            )
        )
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_conversation_retention_uses_last_activity_not_created_at(
    app_fixture: FastAPI,
) -> None:
    """Conversation retention: старый created_at, свежий last_activity_at → НЕ удалён."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory
    settings = _make_retention_settings(message_retention_days=7)

    async with factory() as session:
        from app.db.models import Role, User

        role = Role(workspace_id=workspace_id, name="admin", policy={})
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="retention@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()

        # Создаём диалог с старым created_at, но свежим last_activity_at
        old_created = datetime.now(UTC) - timedelta(days=90)
        conv = Conversation(
            workspace_id=workspace_id,
            user_id=user.id,
            title="old but active",
            archived=False,
            last_activity_at=datetime.now(UTC),
        )
        # Принудительно устанавливаем старый created_at
        conv.created_at = old_created
        session.add(conv)
        await session.commit()
        conv_id = conv.id

    async with factory() as session:
        counts = await retention_cleanup(session, settings, workspace_id)
        assert counts["conversations"] == 0  # Не удалён — last_activity_at свежий

    async with factory() as session:
        result = await session.get(Conversation, conv_id)
        assert result is not None  # Диалог выжил


@pytest.mark.asyncio
async def test_conversation_retention_deletes_inactive_old(
    app_fixture: FastAPI,
) -> None:
    """Conversation retention: старый last_activity_at → удалён, messages cascade."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory
    settings = _make_retention_settings(message_retention_days=7)

    async with factory() as session:
        from app.db.models import Role, User

        role = Role(workspace_id=workspace_id, name="admin", policy={})
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="inactive@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()

        old_date = datetime.now(UTC) - timedelta(days=30)
        conv = Conversation(
            workspace_id=workspace_id,
            user_id=user.id,
            title="old inactive",
            archived=False,
            last_activity_at=old_date,
        )
        session.add(conv)
        await session.flush()

        msg = Message(
            workspace_id=workspace_id,
            conversation_id=conv.id,
            role="user",
            content="old message",
            meta={},
        )
        session.add(msg)
        await session.commit()
        conv_id = conv.id
        msg_id = msg.id

    async with factory() as session:
        counts = await retention_cleanup(session, settings, workspace_id)
        assert counts["conversations"] >= 1

    async with factory() as session:
        result = await session.get(Conversation, conv_id)
        assert result is None
        result = await session.get(Message, msg_id)
        assert result is None  # cascade


@pytest.mark.asyncio
async def test_conversation_retention_disabled_by_default(app_fixture: FastAPI) -> None:
    """message_retention_days=0 → conversation не удаляются."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory
    settings = _make_retention_settings(message_retention_days=0)

    async with factory() as session:
        from app.db.models import Role, User

        role = Role(workspace_id=workspace_id, name="admin", policy={})
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="keep@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()

        old_date = datetime.now(UTC) - timedelta(days=365)
        conv = Conversation(
            workspace_id=workspace_id,
            user_id=user.id,
            title="very old",
            archived=False,
            last_activity_at=old_date,
        )
        session.add(conv)
        await session.commit()
        conv_id = conv.id

    async with factory() as session:
        counts = await retention_cleanup(session, settings, workspace_id)
        assert counts["conversations"] == 0

    async with factory() as session:
        result = await session.get(Conversation, conv_id)
        assert result is not None


@pytest.mark.asyncio
async def test_retention_noop_when_all_zero(app_fixture: FastAPI) -> None:
    """Все retention=0 → ничего не удалено."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory
    settings = _make_retention_settings(
        span_retention_days=0,
        usage_event_retention_days=0,
        message_retention_days=0,
    )

    async with factory() as session:
        from app.db.models import Trace

        old_date = datetime.now(UTC) - timedelta(days=365)
        trace = Trace(
            workspace_id=workspace_id,
            user_id=None,
            ts=old_date,
            status="ok",
            created_at=old_date,
        )
        session.add(trace)
        await session.commit()
        trace_id = trace.id

    async with factory() as session:
        counts = await retention_cleanup(session, settings, workspace_id)
        assert sum(counts.values()) == 0

    async with factory() as session:
        from app.db.models import Trace

        result = await session.get(Trace, trace_id)
        assert result is not None


# ---------------------------------------------------------------------------
# catch_up_missing_days guard test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catch_up_guard_skips_days_older_than_retention(
    app_fixture: FastAPI,
) -> None:
    """catch_up_missing_days: не backfill-ит дни старше retention — агрегаты сохранены."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory

    # Создаём usage_event 100 дней назад
    old_date = datetime.now(UTC) - timedelta(days=100)
    async with factory() as session:
        event = UsageEvent(
            workspace_id=workspace_id,
            user_id=None,
            model_id=None,
            conversation_id=None,
            message_id=None,
            ts=old_date,
            status="ok",
            tokens_in=10,
            tokens_out=5,
        )
        session.add(event)
        await session.commit()

    # Агрегируем этот день
    day = old_date.date()
    async with factory() as session:
        await aggregate_day(session, workspace_id, day)

    # Проверяем агрегат существует
    async with factory() as session:
        result = await session.execute(
            select(UsageDaily).where(
                UsageDaily.workspace_id == workspace_id,
                UsageDaily.date == day.isoformat(),
            )
        )
        assert result.scalar_one_or_none() is not None

    # Purge events через retention (90 дней)
    settings = _make_retention_settings(usage_event_retention_days=90)
    async with factory() as session:
        await retention_cleanup(session, settings, workspace_id)

    # Events удалены
    async with factory() as session:
        result = await session.execute(
            select(UsageEvent).where(UsageEvent.workspace_id == workspace_id)
        )
        assert len(result.scalars().all()) == 0

    # catch_up с retention_days=90 — не должен backfill-ить день старше 90 дней
    today = datetime.now(UTC).date()
    async with factory() as session:
        await catch_up_missing_days(session, workspace_id, today=today, retention_days=90)
        # Если last_aggregated_date == day (100 дней назад), start_day = day+1
        # retention_cutoff = today - 90 = 10 дней назад
        # start_day (day+1 = 99 дней назад) <= retention_cutoff → start_day = retention_cutoff+1
        # start_day = 10 дней назад + 1 = 9 дней назад, yesterday = today-1
        # Если 9 дней назад <= yesterday → backfill от 9 дней назад до yesterday
        # Но events за эти дни уже purged → aggregate_day обнулит агрегаты!
        # Нет — events 100 дней назад purged, events за последние 9 дней есть.
        # На самом деле мы создали только 1 event 100 дней назад.
        # catch_up: last = day (100 дней назад), start = day+1 (99 дней назад)
        # retention_cutoff = today-90 (10 дней назад)
        # 99 дней назад <= 10 дней назад → start_day = 11 дней назад
        # Но yesterday = today-1. start_day (11 days ago) <= yesterday → backfill 11..yesterday
        # Events за 11..yesterday нет → aggregate_day обнулит (но агрегатов за эти дни не было)
        # Проверяем: агрегат за day (100 дней назад) не тронут

    # Проверяем: агрегат за 100-дневный день сохранён
    async with factory() as session:
        result = await session.execute(
            select(UsageDaily).where(
                UsageDaily.workspace_id == workspace_id,
                UsageDaily.date == day.isoformat(),
            )
        )
        daily = result.scalar_one_or_none()
        assert daily is not None
        assert daily.requests >= 1  # Агрегат не обнулён


@pytest.mark.asyncio
async def test_retention_workspace_filter(app_fixture: FastAPI) -> None:
    """Cleanup не затрагивает данные другого workspace (ADR-3)."""
    workspace_id = app_fixture.state.workspace_id
    factory = app_fixture.state.db_session_factory
    settings = _make_retention_settings(span_retention_days=30)

    # Создаём второй workspace
    async with factory() as session:
        other_ws = Workspace(name="other-ws")
        session.add(other_ws)
        await session.flush()
        other_ws_id = other_ws.id

        from app.db.models import Trace

        old_date = datetime.now(UTC) - timedelta(days=60)
        # Span в other workspace (старый)
        other_trace = Trace(
            workspace_id=other_ws_id,
            user_id=None,
            ts=old_date,
            status="ok",
            created_at=old_date,
        )
        session.add(other_trace)
        await session.flush()

        other_span = Span(
            workspace_id=other_ws_id,
            trace_id=other_trace.id,
            name="other_ws_span",
            started_at=old_date,
            payload={},
            created_at=old_date,
        )
        session.add(other_span)
        await session.commit()
        other_span_id = other_span.id

    # Cleanup в основном workspace
    async with factory() as session:
        await retention_cleanup(session, settings, workspace_id)

    # Span в other workspace не тронут
    async with factory() as session:
        result = await session.get(Span, other_span_id)
        assert result is not None
