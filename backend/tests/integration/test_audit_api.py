"""Интеграционные тесты audit log API (T-317).

Access control: non-admin → 404.
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Role, User, Workspace
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI


async def _login_as_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
) -> None:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        ws = Workspace(name=f"test-{role_name}")
        session.add(ws)
        await session.flush()

        role = Role(
            workspace_id=ws.id,
            name=role_name,
            policy=BUILTIN_ROLES[role_name].model_dump(),
            is_builtin=True,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=ws.id,
            role_id=role.id,
            email=f"{role_name}@test.local",
            password_hash=hash_password("test1234"),
            is_active=True,
        )
        session.add(user)
        await session.flush()

        app_fixture.state.workspace_id = ws.id

        cookie = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, cookie)


@pytest.mark.asyncio
async def test_audit_log_denied_for_developer(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer without admin wildcard gets 404 on audit-log."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/audit-log")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_audit_actions_denied_for_developer(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer without admin wildcard gets 404 on audit-log/actions."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/audit-log/actions")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_audit_log_allowed_for_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin with wildcard gets 200 on audit-log."""
    await _login_as_role(api_client, app_fixture, "admin")

    resp = await api_client.get("/api/audit-log")
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_audit_actions_allowed_for_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin with wildcard gets 200 on audit-log/actions."""
    await _login_as_role(api_client, app_fixture, "admin")

    resp = await api_client.get("/api/audit-log/actions")
    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data
    assert isinstance(data["actions"], list)
