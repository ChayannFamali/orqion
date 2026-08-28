"""Т-506: настройки RAG-поиска уровня рабочей области — API.

GET /api/rag-settings — чтение для всех авторизованных.
PUT /api/rag-settings — изменение с правом управления корпусами;
пишет одно действие аудита ``rag_settings.changed`` со старым/новым по обоим
полям; сохранение без изменений запись не создаёт (no-op).
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import AuditLog, RagSettings, Role, User
from fastapi import FastAPI
from sqlalchemy import select


async def _login_as_admin(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=ws_id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=ws_id,
            email="admin@orqion.local",
            password_hash=hash_password("admin-password-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)


async def _login_as_role(
    api_client: httpx.AsyncClient, app_fixture: FastAPI, role_name: str
) -> None:
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=ws_id,
            name=role_name,
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=ws_id,
            email=f"rag-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_defaults_when_no_row(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.get("/api/rag-settings")
    assert resp.status_code == 200
    assert resp.json() == {"relevance_threshold": 0, "max_fragments": 8}


@pytest.mark.asyncio
async def test_get_requires_auth(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.get("/api/rag-settings")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_updates_and_writes_audit(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.put(
        "/api/rag-settings", json={"relevance_threshold": 60, "max_fragments": 4}
    )
    assert resp.status_code == 200
    assert resp.json() == {"relevance_threshold": 60, "max_fragments": 4}

    # Значения сохранены
    resp = await api_client.get("/api/rag-settings")
    assert resp.json() == {"relevance_threshold": 60, "max_fragments": 4}

    # Одно действие аудита со старым/новым по обоим полям
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    changed = [r for r in rows if r.action == "rag_settings.changed"]
    assert len(changed) == 1
    assert changed[0].meta["old"] == {"relevance_threshold": 0, "max_fragments": 8}
    assert changed[0].meta["new"] == {"relevance_threshold": 60, "max_fragments": 4}


@pytest.mark.asyncio
async def test_put_noop_does_not_write_audit(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_as_admin(api_client, app_fixture)

    # Первое изменение — создаёт строку и пишет аудит
    await api_client.put("/api/rag-settings", json={"relevance_threshold": 60, "max_fragments": 4})
    # Повтор с теми же значениями — no-op, аудита не прибавляется
    resp = await api_client.put(
        "/api/rag-settings", json={"relevance_threshold": 60, "max_fragments": 4}
    )
    assert resp.status_code == 200

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "rag_settings.changed")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_put_partial_change_records_both_fields(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Меняется одно поле — в аудите всё равно старое/новое по обоим."""
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.put(
        "/api/rag-settings", json={"relevance_threshold": 30, "max_fragments": 8}
    )
    assert resp.status_code == 200

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        row = (
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "rag_settings.changed")
                )
            )
            .scalars()
            .all()
        )
    assert len(row) == 1
    assert row[0].meta["new"] == {"relevance_threshold": 30, "max_fragments": 8}


# ---------------------------------------------------------------------------
# Доступ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_forbidden_without_manage_corpora(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Роль без права управления корпусами — изменение недоступно (404)."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.put(
        "/api/rag-settings", json={"relevance_threshold": 50, "max_fragments": 4}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_allowed_without_manage_corpora(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Чтение настроек доступно роли без права управления корпусами."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/rag-settings")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_put_allowed_for_architect(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Роль с правом управления корпусами может менять настройки."""
    await _login_as_role(api_client, app_fixture, "architect")

    resp = await api_client.put(
        "/api/rag-settings", json={"relevance_threshold": 20, "max_fragments": 6}
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Валидация
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_rejects_out_of_range(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_as_admin(api_client, app_fixture)

    for body in (
        {"relevance_threshold": 101, "max_fragments": 4},
        {"relevance_threshold": -1, "max_fragments": 4},
        {"relevance_threshold": 50, "max_fragments": 0},
        {"relevance_threshold": 50, "max_fragments": 9},
    ):
        resp = await api_client.put("/api/rag-settings", json=body)
        assert resp.status_code == 422, body


@pytest.mark.asyncio
async def test_unique_row_per_workspace(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Несколько сохранений не создают дублей — одна строка на область."""
    await _login_as_admin(api_client, app_fixture)

    await api_client.put("/api/rag-settings", json={"relevance_threshold": 10, "max_fragments": 3})
    await api_client.put("/api/rag-settings", json={"relevance_threshold": 20, "max_fragments": 5})

    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        rows = (
            (await session.execute(select(RagSettings).where(RagSettings.workspace_id == ws_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].relevance_threshold == 20
    assert rows[0].max_fragments == 5
