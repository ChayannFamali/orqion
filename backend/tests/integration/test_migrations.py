"""Тест миграций: upgrade head → downgrade base → upgrade head.

Также проверяет, что ORM-модели согласованы со схемой, созданной миграцией
(BUG-004: Trace/Span не имели TimestampMixin, но миграция создавала created_at NOT NULL).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent.parent / "alembic.ini"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "db" / "migrations"


def _make_config(db_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def test_migration_roundtrip(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path}/migrate_test.db"
    config = _make_config(db_url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_trace_span_insert_after_migration(tmp_path: Path) -> None:
    """BUG-004: insert trace/span в БД, созданную через alembic upgrade (не create_all).

    До фикса: Trace/Span не имели TimestampMixin → created_at не замаплен →
    insert падал с NOT NULL violation на migration-created БД.
    """
    from app.db.models import Span, Trace, Workspace

    db_url = f"sqlite:///{tmp_path}/migrate_orm_test.db"
    config = _make_config(db_url)
    command.upgrade(config, "head")

    engine = create_engine(db_url)
    with Session(engine) as session:
        # workspace — FK target, нужен для insert
        ws = Workspace(name="test-ws")
        session.add(ws)
        session.flush()

        trace = Trace(
            workspace_id=ws.id,
            user_id=None,
            ts=datetime.now(UTC),
            status="ok",
        )
        session.add(trace)
        session.flush()

        span = Span(
            workspace_id=ws.id,
            trace_id=trace.id,
            name="step_search",
            started_at=datetime.now(UTC),
            payload={"step": "step_search"},
        )
        session.add(span)
        session.commit()

        # Проверяем, что created_at заполнен
        result = session.execute(select(Trace).where(Trace.id == trace.id))
        saved_trace = result.scalar_one()
        assert saved_trace.created_at is not None

        result = session.execute(select(Span).where(Span.id == span.id))
        saved_span = result.scalar_one()
        assert saved_span.created_at is not None

    engine.dispose()
