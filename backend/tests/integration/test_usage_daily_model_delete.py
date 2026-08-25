"""BUG-019: удаление модели с историей в usage_daily.

Регрессия против схемы, созданной alembic-миграциями (не create_all):
миграция 0021 (BUG-008) убрала внешние ключи usage_daily.user_id/model_id
на PostgreSQL, но на SQLite они остались — и удаление модели с суточной
историей падало FOREIGN KEY constraint failed → HTTP 500. Фикс: миграция
0027 убирает пережившие FK на SQLite; delete_model переводит строки
агрегата на сентинел NIL_ID (обнуление невозможно — model_id часть PK;
удаление противоречило бы §5.3 — агрегат хранится бессрочно).

Покрытие:
- схема: после миграций у usage_daily нет FK на user и model (SQLite,
  мигрированная БД — прецедент тестов 0013/0024);
- диалект-гейт 0027: на не-SQLite upgrade()/downgrade() без DDL;
- репро: удаление модели со строками в usage_daily на мигрированной
  SQLite-БД → 200 (раньше 500), строки переходят на NIL_ID;
- семантика (оба диалекта, штатная фикстура): перенос на сентинел и
  мердж в существующую сентинел-строку.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Model, Role, UsageDaily, User
from app.policy.presets import BUILTIN_ROLES
from app.usage.constants import NIL_ID
from fastapi import FastAPI
from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection

MODEL_BODY = {"alias": "bug019-model", "upstream_name": "upstream-bug019"}

_PG_URL = os.environ.get("ORQION_DATABASE_URL", "")
SKIP_ON_POSTGRES = pytest.mark.skipif(
    _PG_URL.startswith(("postgres://", "postgresql://")),
    reason="SQLite-only: мигрированная схема и FK-репро",
)


async def _login(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> str:
    """Создаёт admin-пользователя, логинит, возвращает user.id."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email="bug019-admin@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _create_provider_and_model(api_client: httpx.AsyncClient) -> str:
    resp = await api_client.post(
        "/api/providers",
        json={"kind": "external", "base_url": "http://api.test/v1", "enabled": True},
    )
    assert resp.status_code == 201, resp.text
    provider_id = str(resp.json()["id"])
    resp = await api_client.post(f"/api/providers/{provider_id}/models", json=MODEL_BODY)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _seed_usage_daily(
    app_fixture: FastAPI,
    user_id: str,
    model_id: str,
    *,
    requests: int,
    tokens_in: int,
    tokens_out: int,
    cost: float,
    errors: int,
    avg_latency_ms: int | None,
    date: str = "2026-08-20",
) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        session.add(
            UsageDaily(
                workspace_id=workspace_id,
                date=date,
                user_id=user_id,
                model_id=model_id,
                requests=requests,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=cost,
                errors=errors,
                avg_latency_ms=avg_latency_ms,
            )
        )
        await session.commit()


async def _usage_daily_rows(app_fixture: FastAPI) -> list[UsageDaily]:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        return list((await session.execute(select(UsageDaily))).scalars().all())


async def _model_exists(app_fixture: FastAPI, model_id: str) -> bool:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(select(Model).where(Model.id == model_id))
        return result.scalar_one_or_none() is not None


async def _assert_deleted_and_sentinel(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    model_id: str,
    user_id: str,
) -> None:
    resp = await api_client.delete(f"/api/providers/models/{model_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True
    assert await _model_exists(app_fixture, model_id) is False

    rows = await _usage_daily_rows(app_fixture)
    assert len(rows) == 1
    row = rows[0]
    assert row.model_id == NIL_ID
    assert row.user_id == user_id
    assert row.requests == 3
    assert row.tokens_in == 100
    assert row.tokens_out == 50
    assert row.cost == pytest.approx(0.5)
    assert row.errors == 1
    assert row.avg_latency_ms == 120


# ---------------------------------------------------------------------------
# Схема: после миграций у usage_daily нет FK на user и model (SQLite)
# ---------------------------------------------------------------------------


@SKIP_ON_POSTGRES
@pytest.mark.asyncio
async def test_usage_daily_no_user_model_fks_after_migrations(
    app_migrated_fixture: FastAPI,
) -> None:
    engine = app_migrated_fixture.state.db_engine

    def _fk_refs(conn: Connection) -> set[str]:
        insp = inspect(conn)
        return {str(fk["referred_table"]) for fk in insp.get_foreign_keys("usage_daily")}

    async with engine.connect() as conn:
        refs = await conn.run_sync(_fk_refs)

    assert "user" not in refs, f"usage_daily.user_id всё ещё ссылается на user: {refs}"
    assert "model" not in refs, f"usage_daily.model_id всё ещё ссылается на model: {refs}"


# ---------------------------------------------------------------------------
# Диалект-гейт 0027: на не-SQLite — без DDL (прецедент тестов 0013/0024)
# ---------------------------------------------------------------------------


def test_migration_0027_skips_on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """На не-SQLite диалекте 0027 ничего не выполняет (схема уже от 0021)."""
    import importlib.util
    from unittest.mock import MagicMock

    migration_path = (
        Path(__file__).resolve().parent.parent.parent
        / "app"
        / "db"
        / "migrations"
        / "versions"
        / "0027_usage_daily_drop_leftover_sqlite_fks.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0027_test", migration_path)
    assert spec is not None
    assert spec.loader is not None
    migration_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_mod)

    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    monkeypatch.setattr("alembic.op.get_bind", lambda: mock_bind)

    migration_mod.upgrade()
    mock_bind.execute.assert_not_called()
    migration_mod.downgrade()
    mock_bind.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Репро: мигрированная SQLite-БД (раньше: 500)
# ---------------------------------------------------------------------------


@SKIP_ON_POSTGRES
@pytest.mark.asyncio
async def test_delete_model_with_usage_daily_history_on_migrated_sqlite(
    migrated_api_client: httpx.AsyncClient, app_migrated_fixture: FastAPI
) -> None:
    user_id = await _login(migrated_api_client, app_migrated_fixture)
    model_id = await _create_provider_and_model(migrated_api_client)
    await _seed_usage_daily(
        app_migrated_fixture,
        user_id,
        model_id,
        requests=3,
        tokens_in=100,
        tokens_out=50,
        cost=0.5,
        errors=1,
        avg_latency_ms=120,
    )
    await _assert_deleted_and_sentinel(migrated_api_client, app_migrated_fixture, model_id, user_id)


# ---------------------------------------------------------------------------
# Семантика (оба диалекта): перенос на сентинел и мердж
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_model_moves_usage_daily_to_sentinel(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Строки агрегата сохраняются, model_id → NIL_ID (по §5.3 — бессрочно)."""
    user_id = await _login(api_client, app_fixture)
    model_id = await _create_provider_and_model(api_client)
    await _seed_usage_daily(
        app_fixture,
        user_id,
        model_id,
        requests=3,
        tokens_in=100,
        tokens_out=50,
        cost=0.5,
        errors=1,
        avg_latency_ms=120,
    )
    await _assert_deleted_and_sentinel(api_client, app_fixture, model_id, user_id)


@pytest.mark.asyncio
async def test_delete_model_merges_usage_daily_into_existing_sentinel(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Сентинел-строка уже есть (ранее удалённая модель) — мердж счётчиков."""
    user_id = await _login(api_client, app_fixture)
    model_id = await _create_provider_and_model(api_client)

    await _seed_usage_daily(
        app_fixture,
        user_id,
        NIL_ID,
        requests=1,
        tokens_in=10,
        tokens_out=5,
        cost=0.1,
        errors=0,
        avg_latency_ms=100,
    )
    await _seed_usage_daily(
        app_fixture,
        user_id,
        model_id,
        requests=3,
        tokens_in=100,
        tokens_out=50,
        cost=0.5,
        errors=1,
        avg_latency_ms=200,
    )

    resp = await api_client.delete(f"/api/providers/models/{model_id}")
    assert resp.status_code == 200, resp.text

    rows = await _usage_daily_rows(app_fixture)
    assert len(rows) == 1
    row = rows[0]
    assert row.model_id == NIL_ID
    assert row.requests == 4
    assert row.tokens_in == 110
    assert row.tokens_out == 55
    assert row.cost == pytest.approx(0.6)
    assert row.errors == 1
    # Среднее не пересчитывается — остаётся значение существующей строки
    assert row.avg_latency_ms == 100
