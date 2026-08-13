"""Тест users API: список, детали, смена роли, смена статуса, access control,
self-edit блок, impersonation, nested impersonation блок, exit-impersonation,
истёкшая родительская сессия, audit_log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import AuditLog, Role, Session, User
from fastapi import FastAPI
from sqlalchemy import select


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> str:
    """Создаёт admin-пользователя и логинится через cookie. Возвращает user_id."""
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

        password = "admin-password-123"
        user = User(
            workspace_id=ws_id,
            email="admin@orqion.local",
            password_hash=hash_password(password),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _login_as_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
    email_suffix: str = "",
) -> str:
    """Логинит пользователя с заданной ролью. Возвращает user_id."""
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
            email=f"users-{role_name}{email_suffix}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _create_target_user(
    app_fixture: FastAPI,
    role_name: str = "developer",
    email: str = "target@orqion.local",
) -> str:
    """Создаёт пользователя в БД и возвращает его ID (без логина)."""
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
            email=email,
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.commit()
        return user.id


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_users(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/users → список пользователей."""
    admin_id = await _login_as_admin(api_client, app_fixture)
    await _create_target_user(app_fixture, "developer", "dev@orqion.local")

    resp = await api_client.get("/api/users")
    assert resp.status_code == 200
    users = resp.json()["users"]
    ids = [u["id"] for u in users]
    assert admin_id in ids

    dev = next(u for u in users if u["email"] == "dev@orqion.local")
    assert dev["role_name"] == "developer"
    assert dev["is_builtin_role"] is True


@pytest.mark.asyncio
async def test_list_users_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/users → 404 для non-admin."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/users")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_detail(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/users/{id} → детали пользователя."""
    await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "detail@orqion.local")

    resp = await api_client.get(f"/api/users/{target_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "detail@orqion.local"
    assert data["role_name"] == "developer"


@pytest.mark.asyncio
async def test_get_user_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/users/{id} с несуществующим ID → 404."""
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.get("/api/users/nonexistent-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update — role change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_user_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH /api/users/{id} — смена роли."""
    await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "role-change@orqion.local")

    # Находим role_id для support
    list_resp = await api_client.get("/api/roles")
    roles = list_resp.json()["roles"]
    support_role_id = next(r["id"] for r in roles if r["name"] == "support")

    resp = await api_client.patch(
        f"/api/users/{target_id}",
        json={"role_id": support_role_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["role_name"] == "support"


@pytest.mark.asyncio
async def test_update_user_role_audit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH с сменой role_id → audit_log user.role_changed."""
    admin_id = await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "audit-role@orqion.local")

    list_resp = await api_client.get("/api/roles")
    roles = list_resp.json()["roles"]
    support_role_id = next(r["id"] for r in roles if r["name"] == "support")

    resp = await api_client.patch(
        f"/api/users/{target_id}",
        json={"role_id": support_role_id},
    )
    assert resp.status_code == 200

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == "user.role_changed",
                AuditLog.object_id == target_id,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.actor_user_id == admin_id
        assert audit.meta["old_role_id"] != audit.meta["new_role_id"]
        assert audit.meta["target_email"] == "audit-role@orqion.local"


# ---------------------------------------------------------------------------
# Update — status change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_user_deactivate(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH /api/users/{id} — деактивация."""
    await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "deactivate@orqion.local")

    resp = await api_client.patch(
        f"/api/users/{target_id}",
        json={"is_active": False},
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_update_user_status_audit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH с сменой is_active → audit_log user.status_changed."""
    admin_id = await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "audit-status@orqion.local")

    resp = await api_client.patch(
        f"/api/users/{target_id}",
        json={"is_active": False},
    )
    assert resp.status_code == 200

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == "user.status_changed",
                AuditLog.object_id == target_id,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.actor_user_id == admin_id
        assert audit.meta["old"] is True
        assert audit.meta["new"] is False


# ---------------------------------------------------------------------------
# Self-edit block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_edit_blocked(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH на собственный id → 400, независимо от поля."""
    admin_id = await _login_as_admin(api_client, app_fixture)

    resp = await api_client.patch(
        f"/api/users/{admin_id}",
        json={"is_active": False},
    )
    assert resp.status_code == 400
    assert resp.json()["reason"]


@pytest.mark.asyncio
async def test_self_edit_role_blocked(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH на собственный id со сменой роли → 400."""
    admin_id = await _login_as_admin(api_client, app_fixture)

    list_resp = await api_client.get("/api/roles")
    roles = list_resp.json()["roles"]
    support_role_id = next(r["id"] for r in roles if r["name"] == "support")

    resp = await api_client.patch(
        f"/api/users/{admin_id}",
        json={"role_id": support_role_id},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Update — access control + not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_user_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH /api/users/{id} → 404 для non-admin."""
    await _login_as_role(api_client, app_fixture, "developer")
    target_id = await _create_target_user(app_fixture, "developer", "non-admin-target@orqion.local")

    resp = await api_client.patch(
        f"/api/users/{target_id}",
        json={"is_active": False},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_user_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH /api/users/{id} с несуществующим ID → 404."""
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.patch(
        "/api/users/nonexistent-id",
        json={"is_active": False},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_user_invalid_role_id(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH с несуществующим role_id → 404."""
    await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "bad-role@orqion.local")

    resp = await api_client.patch(
        f"/api/users/{target_id}",
        json={"role_id": "nonexistent-role-id"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Impersonation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_impersonate_user(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/users/{id}/impersonate → 200, cookie установлена, me показывает имперсонацию."""
    await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "impersonate@orqion.local")

    resp = await api_client.post(f"/api/users/{target_id}/impersonate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "impersonating"

    # Проверяем что /me показывает имперсонацию
    me_resp = await api_client.get("/api/auth/me")
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["is_impersonating"] is True
    assert me_data["email"] == "impersonate@orqion.local"
    assert me_data["impersonated_by_email"] == "admin@orqion.local"
    assert me_data["capabilities"] == ["chat", "upload", "custom_prompts", "view_traces"]


@pytest.mark.asyncio
async def test_impersonate_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/users/{id}/impersonate → 404 для non-admin."""
    await _login_as_role(api_client, app_fixture, "developer")
    target_id = await _create_target_user(app_fixture, "support", "imp-target@orqion.local")

    resp = await api_client.post(f"/api/users/{target_id}/impersonate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_impersonate_writes_audit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST impersonate → audit_log action='impersonate'."""
    admin_id = await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "audit-imp@orqion.local")

    resp = await api_client.post(f"/api/users/{target_id}/impersonate")
    assert resp.status_code == 200

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == "impersonate",
                AuditLog.object_id == target_id,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.actor_user_id == admin_id


# ---------------------------------------------------------------------------
# Nested impersonation block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nested_impersonation_blocked(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST impersonate из уже имперсонационной сессии → 400."""
    await _login_as_admin(api_client, app_fixture)
    target1_id = await _create_target_user(app_fixture, "developer", "nested-1@orqion.local")
    target2_id = await _create_target_user(
        app_fixture,
        "support",
        "nested-2@orqion.local",
    )

    # Первая имперсонация — OK
    resp = await api_client.post(f"/api/users/{target1_id}/impersonate")
    assert resp.status_code == 200

    # Попытка второй имперсонации из первой — 400
    resp = await api_client.post(f"/api/users/{target2_id}/impersonate")
    assert resp.status_code == 400
    assert resp.json()["reason"]


# ---------------------------------------------------------------------------
# Exit impersonation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_impersonation_restores_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/auth/exit-impersonation → восстанавливает админскую сессию."""
    await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "exit-imp@orqion.local")

    resp = await api_client.post(f"/api/users/{target_id}/impersonate")
    assert resp.status_code == 200

    # Exit
    resp = await api_client.post("/api/auth/exit-impersonation")
    assert resp.status_code == 200
    assert resp.json()["status"] == "restored"

    # Проверяем что мы снова admin
    me_resp = await api_client.get("/api/auth/me")
    me_data = me_resp.json()
    assert me_data["is_impersonating"] is False
    assert me_data["email"] == "admin@orqion.local"
    assert me_data["capabilities"] == ["*"]


@pytest.mark.asyncio
async def test_exit_impersonation_not_in_impersonation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST exit-impersonation без активной имперсонации → 400."""
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.post("/api/auth/exit-impersonation")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_exit_impersonation_expired_parent_session(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Если родительская сессия истекла → полный logout."""
    await _login_as_admin(api_client, app_fixture)
    target_id = await _create_target_user(app_fixture, "developer", "expired-parent@orqion.local")

    # Имперсонация
    resp = await api_client.post(f"/api/users/{target_id}/impersonate")
    assert resp.status_code == 200

    # Делаем родительскую сессию истёкшей
    factory = app_fixture.state.db_session_factory
    # httpx может хранить несколько cookie с одним именем, берём последний
    cookies = [c for c in api_client.cookies.jar if c.name == COOKIE_NAME]
    impersonation_cookie = cookies[-1].value if cookies else None

    async with factory() as session:
        imp_session = await session.execute(
            select(Session).where(Session.id == impersonation_cookie)
        )
        imp_record = imp_session.scalar_one()
        parent_id = imp_record.impersonated_by
        assert parent_id is not None

        # Истекаем родительскую сессию
        parent_result = await session.execute(select(Session).where(Session.id == parent_id))
        parent = parent_result.scalar_one()
        parent.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    # Exit — должен быть полный logout
    resp = await api_client.post("/api/auth/exit-impersonation")
    assert resp.status_code == 200
    assert resp.json()["status"] == "logged_out"

    # Cookie удалена — /api/auth/me должен вернуть 401
    me_resp = await api_client.get("/api/auth/me")
    assert me_resp.status_code == 401


# ---------------------------------------------------------------------------
# /me shows impersonation info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_without_impersonation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/auth/me без имперсонации → is_impersonating=False."""
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_impersonating"] is False
    assert data["impersonated_by_email"] is None
