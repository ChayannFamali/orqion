"""Тесты T-410: migrate script, load_test script, migration 0013 dialect guard.

BUG-005: проверка dialect guard в миграции 0013 и работа скриптов.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

# -----------------------------------------------------------------------
# Test 3: migration 0013 dialect guard — FTS5/vec_chunk_map only on SQLite
# -----------------------------------------------------------------------


def test_migration_0013_skips_on_postgres(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """BUG-005: migration 0013 dialect guard — skip FTS5/vec0 on non-SQLite.

    На SQLite миграция создаёт fts_chunks + vec_chunk_map (проверяем наличие).
    Dialect guard проверяется через mock: если dialect != sqlite,
    upgrade()/downgrade() должны вернуть без выполнения DDL.
    """
    from unittest.mock import MagicMock

    from alembic import command
    from alembic.config import Config

    ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent.parent / "alembic.ini"
    MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "db" / "migrations"

    db_url = f"sqlite:///{tmp_path}/migrate_0013_test.db"
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(config, "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    # На SQLite: fts_chunks и vec_chunk_map должны существовать
    assert "fts_chunks" in table_names, "fts_chunks must exist on SQLite"
    assert "vec_chunk_map" in table_names, "vec_chunk_map must exist on SQLite"
    engine.dispose()

    # Прямая проверка dialect guard: вызываем upgrade/downgrade с mock bind (postgresql)
    migration_path = (
        Path(__file__).resolve().parent.parent.parent
        / "app"
        / "db"
        / "migrations"
        / "versions"
        / "0013_vector_store.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0013_test", migration_path)
    assert spec is not None
    assert spec.loader is not None
    migration_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_mod)

    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    monkeypatch.setattr("alembic.op.get_bind", lambda: mock_bind)

    # upgrade() на "postgresql" — должен вернуть без выполнения DDL
    migration_mod.upgrade()
    mock_bind.execute.assert_not_called()

    # downgrade() на "postgresql" — тоже должен вернуть
    migration_mod.downgrade()
    mock_bind.execute.assert_not_called()


def test_migration_0024_skips_on_postgres(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """BUG-017: миграция 0024 (fts_messages) повторяет фикс BUG-005.

    На SQLite миграция создаёт fts_messages. На не-SQLite диалекте
    upgrade()/downgrade() обязаны вернуть без выполнения DDL —
    CREATE VIRTUAL TABLE ... fts5 на PostgreSQL падает с
    syntax error at or near "VIRTUAL".
    """
    from unittest.mock import MagicMock

    from alembic import command
    from alembic.config import Config

    ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent.parent / "alembic.ini"
    MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "db" / "migrations"

    db_url = f"sqlite:///{tmp_path}/migrate_0024_test.db"
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", db_url)

    command.upgrade(config, "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    # На SQLite: fts_messages должна существовать
    assert "fts_messages" in table_names, "fts_messages must exist on SQLite"
    engine.dispose()

    # Прямая проверка dialect guard: вызываем upgrade/downgrade с mock bind (postgresql)
    migration_path = (
        Path(__file__).resolve().parent.parent.parent
        / "app"
        / "db"
        / "migrations"
        / "versions"
        / "0024_fts_messages.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0024_test", migration_path)
    assert spec is not None
    assert spec.loader is not None
    migration_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_mod)

    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    monkeypatch.setattr("alembic.op.get_bind", lambda: mock_bind)

    # upgrade() на "postgresql" — должен вернуть без выполнения DDL
    migration_mod.upgrade()
    mock_bind.execute.assert_not_called()

    # downgrade() на "postgresql" — тоже должен вернуть
    migration_mod.downgrade()
    mock_bind.execute.assert_not_called()


# -----------------------------------------------------------------------
# Test 1: migrate_sqlite_to_postgres — row counts + content verification
# -----------------------------------------------------------------------


def test_migrate_sqlite_to_postgres(tmp_path: Path) -> None:
    """T-410: скрипт миграции копирует данные SQLite→SQLite (имитация PostgreSQL).

    Проверяет:
    - row counts совпадают для каждой перенесённой таблицы
    - содержимое: provider.api_key_enc и span.payload (JSON) побайтово совпадают
    - idempotent: повторный запуск на непустую целевую БД → RuntimeError
    """
    pytest.importorskip("aiosqlite")

    from app.db.base import Base
    from app.db.models import Model, Provider, Role, Span, Trace, User, Workspace

    # --- Source: создаём БД с данными ---
    source_url = f"sqlite:///{tmp_path}/source.db"
    source_engine = create_engine(source_url)
    Base.metadata.create_all(source_engine)

    with Session(source_engine) as session:
        ws = Workspace(name="test-ws")
        session.add(ws)
        session.flush()

        role = Role(workspace_id=ws.id, name="admin", is_builtin=True, policy={"allow": "*"})
        session.add(role)
        session.flush()

        user = User(
            workspace_id=ws.id,
            email="test@example.com",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        session.flush()

        provider = Provider(
            workspace_id=ws.id,
            kind="openai",
            base_url="https://api.openai.com/v1",
            api_key_enc="encrypted_secret_key_data",
            enabled=True,
            capabilities={"chat": True, "streaming": True},
        )
        session.add(provider)
        session.flush()

        model = Model(
            workspace_id=ws.id,
            provider_id=provider.id,
            alias="gpt-4",
            upstream_name="gpt-4",
            locality="external",
            enabled=True,
        )
        session.add(model)
        session.flush()

        trace = Trace(
            workspace_id=ws.id,
            user_id=user.id,
            ts=datetime.now(UTC),
            status="ok",
            total_ms=100,
        )
        session.add(trace)
        session.flush()

        span = Span(
            workspace_id=ws.id,
            trace_id=trace.id,
            name="step_search",
            started_at=datetime.now(UTC),
            payload={"query": "test query", "results": ["r1", "r2"], "count": 2},
        )
        session.add(span)
        session.commit()

    source_engine.dispose()

    # --- Dest: создаём схему (имитация orqion migrate), таблицы пустые ---
    dest_url = f"sqlite:///{tmp_path}/dest.db"
    dest_engine = create_engine(dest_url)
    Base.metadata.create_all(dest_engine)
    dest_engine.dispose()

    # --- Запускаем миграцию ---
    import asyncio

    # Импорт скрипта как модуля — добавляем backend/ в sys.path
    backend_root = Path(__file__).resolve().parent.parent.parent
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    from scripts.migrate_sqlite_to_postgres import migrate

    counts = asyncio.run(migrate(source_url, dest_url))

    # --- Проверка row counts ---
    assert counts["workspace"] == 1
    assert counts["role"] == 1
    assert counts["user"] == 1
    assert counts["provider"] == 1
    assert counts["model"] == 1
    assert counts["trace"] == 1
    assert counts["span"] == 1

    # --- Проверка содержимого ---
    dest_engine = create_engine(dest_url)
    with Session(dest_engine) as session:
        # provider.api_key_enc — побайтово
        dest_provider: Provider = session.execute(select(Provider)).scalar_one()
        assert dest_provider.api_key_enc == "encrypted_secret_key_data"
        assert dest_provider.capabilities == {"chat": True, "streaming": True}

        # span.payload (JSON) — по значению
        dest_span: Span = session.execute(select(Span)).scalar_one()
        assert dest_span.payload == {
            "query": "test query",
            "results": ["r1", "r2"],
            "count": 2,
        }
        assert dest_span.name == "step_search"

        # user.email — точное совпадение
        dest_user: User = session.execute(select(User)).scalar_one()
        assert dest_user.email == "test@example.com"
        assert dest_user.auth_method == "local"

    # --- Idempotent: повторный запуск → RuntimeError ---
    with pytest.raises(RuntimeError, match="не пуста"):
        asyncio.run(migrate(source_url, dest_url))

    dest_engine.dispose()


# -----------------------------------------------------------------------
# Test 2: load_test script runs and produces stats
# -----------------------------------------------------------------------


def _load_test_module() -> Any:
    """Загружает load_test.py как модуль."""
    spec = importlib.util.spec_from_file_location(
        "load_test",
        str(
            Path(__file__).resolve().parent.parent.parent.parent
            / "backend"
            / "scripts"
            / "load_test.py"
        ),
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_test_script_runs() -> None:
    """T-410: load_test.py загружается, функции существуют и корректны.

    CI-тест: проверяет что скрипт не падает. Ручной прогон с реальными числами —
    отдельно, по прецеденту T-202.
    """
    mod = _load_test_module()

    import asyncio

    assert hasattr(mod, "run_load_test")
    assert hasattr(mod, "single_request")
    assert hasattr(mod, "main")

    assert asyncio.iscoroutinefunction(mod.run_load_test)
    assert asyncio.iscoroutinefunction(mod.single_request)


def test_load_test_latency_calculation() -> None:
    """T-410: проверка percentile calculation в load_test с mock-сервером."""
    import asyncio

    import httpx

    mod = _load_test_module()

    async def run_with_mock() -> dict[str, float | int]:
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"response": "ok"}))

        async with httpx.AsyncClient(transport=transport) as client:
            results: dict[str, float | int] = await mod.run_load_test(
                "http://test",
                concurrent=2,
                total_requests=4,
                model="test",
                prompt="hello",
                client=client,
            )
        return results

    results = asyncio.run(run_with_mock())
    assert results["total_requests"] == 4
    assert results["errors"] == 0
    assert results["avg_latency_ms"] > 0
    assert results["p50_latency_ms"] > 0
    assert results["p95_latency_ms"] > 0
    assert results["p99_latency_ms"] > 0
