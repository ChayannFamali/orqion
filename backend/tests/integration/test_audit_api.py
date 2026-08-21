"""Интеграционные тесты audit log API (T-317, T-428).

Access control: non-admin → 404.
"""

from __future__ import annotations

import csv
import io
import json

import httpx
import pytest
from app.audit.service import write_audit
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


# ---------------------------------------------------------------------------
# Export tests (T-428)
# ---------------------------------------------------------------------------


async def _login_as_admin_with_audit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> str:
    """Логинится как admin и создаёт несколько audit-записей."""
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        ws = Workspace(name="admin-export-test")
        session.add(ws)
        await session.flush()

        role = Role(
            workspace_id=ws.id,
            name="admin",
            policy=BUILTIN_ROLES["admin"].model_dump(),
            is_builtin=True,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=ws.id,
            role_id=role.id,
            email="admin@export.test",
            password_hash=hash_password("test1234"),
            is_active=True,
        )
        session.add(user)
        await session.flush()

        # Create audit entries
        await write_audit(
            session, ws.id, user.id, "role.created", "role", role.id, {"name": "admin"}
        )
        await write_audit(
            session,
            ws.id,
            user.id,
            "role.policy_changed",
            "role",
            role.id,
            {"old": {}, "new": {}},
        )
        await write_audit(
            session,
            ws.id,
            user.id,
            "user.created",
            "user",
            user.id,
            {"email": "admin@export.test"},
        )

        app_fixture.state.workspace_id = ws.id
        cookie = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, cookie)
    return user.id


@pytest.mark.asyncio
async def test_export_json_matches_list(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """JSON-экспорт: те же entries, что GET /api/audit-log (та же выборка)."""
    await _login_as_admin_with_audit(api_client, app_fixture)

    # Get via list endpoint (DESC)
    resp_list = await api_client.get("/api/audit-log?limit=100")
    assert resp_list.status_code == 200
    list_data = resp_list.json()
    list_entries = list_data["entries"]

    # Get via export endpoint (ASC)
    resp_export = await api_client.get("/api/audit-log/export?format=json")
    assert resp_export.status_code == 200
    export_data = resp_export.json()
    export_entries = export_data["entries"]

    # Same total count
    assert list_data["total"] == export_data["total"]
    assert len(list_entries) == len(export_entries)

    # Same set of actions (entries may share timestamp, so per-entry order is not comparable)
    list_actions = {e["action"] for e in list_entries}
    export_actions = {e["action"] for e in export_entries}
    assert list_actions == export_actions

    # Headers
    assert resp_export.headers["X-Export-Total"] == str(export_data["total"])
    assert resp_export.headers["X-Export-Count"] == str(len(export_entries))


@pytest.mark.asyncio
async def test_export_csv_format(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """CSV: заголовок, 6 колонок, meta как JSON-строка."""
    await _login_as_admin_with_audit(api_client, app_fixture)

    resp = await api_client.get("/api/audit-log/export?format=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    # Headers present
    assert "X-Export-Total" in resp.headers
    assert "X-Export-Count" in resp.headers

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)

    # Header row
    assert rows[0] == ["ts", "actor_user_id", "action", "object_type", "object_id", "meta"]

    # Data rows: 3 entries
    assert len(rows) == 4  # 1 header + 3 data

    # Verify meta is valid JSON
    for row in rows[1:]:
        meta = json.loads(row[5])
        assert isinstance(meta, dict)


@pytest.mark.asyncio
async def test_export_csv_matches_list(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """CSV: данные соответствуют GET /api/audit-log (reverse order)."""
    await _login_as_admin_with_audit(api_client, app_fixture)

    resp_list = await api_client.get("/api/audit-log?limit=100")
    list_entries = resp_list.json()["entries"]

    resp_csv = await api_client.get("/api/audit-log/export?format=csv")
    reader = csv.reader(io.StringIO(resp_csv.text))
    csv_rows = list(reader)[1:]  # skip header

    # Same count
    assert len(csv_rows) == len(list_entries)

    # Same set of actions (entries may share timestamp, so order within is not comparable)
    csv_actions = {row[2] for row in csv_rows}
    list_actions = {e["action"] for e in list_entries}
    assert csv_actions == list_actions


@pytest.mark.asyncio
async def test_export_denied_for_developer(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Non-admin → 404."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/audit-log/export?format=json")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_export_filter_by_action(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """?action=role.created → только matching entries."""
    await _login_as_admin_with_audit(api_client, app_fixture)

    resp = await api_client.get("/api/audit-log/export?format=json&action=role.created")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exported"] == 1
    assert data["entries"][0]["action"] == "role.created"


@pytest.mark.asyncio
async def test_export_offset_pagination(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """offset пагинация: первая страница + вторая страница."""
    await _login_as_admin_with_audit(api_client, app_fixture)

    # Page 1: limit=2
    resp1 = await api_client.get("/api/audit-log/export?format=json&limit=2&offset=0")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["exported"] == 2
    assert resp1.headers["X-Export-Count"] == "2"
    assert resp1.headers["X-Export-Total"] == "3"

    # Page 2: limit=2, offset=2
    resp2 = await api_client.get("/api/audit-log/export?format=json&limit=2&offset=2")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["exported"] == 1
    assert resp2.headers["X-Export-Count"] == "1"
    assert resp2.headers["X-Export-Total"] == "3"

    # Total entries across pages = 3 (no data loss)
    assert data1["exported"] + data2["exported"] == 3
