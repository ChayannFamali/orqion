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


# ---------------------------------------------------------------------------
# TD-10: Create user via API + change password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/users — admin creates user, gets password in response."""
    await _login_as_admin(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "admin").limit(1)
        )
        admin_role = result.scalar_one()

    response = await api_client.post(
        "/api/users",
        json={"email": "newdev@orqion.local", "role_id": admin_role.id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "newdev@orqion.local"
    assert data["is_active"] is True
    assert data["must_change_password"] is True
    assert "password" in data
    assert len(data["password"]) > 10
    assert data["role_name"] == "admin"

    # Password is not stored in plaintext
    async with factory() as session:
        result = await session.execute(select(User).where(User.email == "newdev@orqion.local"))
        user = result.scalar_one()
        assert user.password_hash != data["password"]
        assert user.must_change_password is True


@pytest.mark.asyncio
async def test_create_user_audit_log(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/users — audit_log entry user.created without password."""
    await _login_as_admin(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "admin").limit(1)
        )
        admin_role = result.scalar_one()

    response = await api_client.post(
        "/api/users",
        json={"email": "auditme@orqion.local", "role_id": admin_role.id},
    )
    assert response.status_code == 200
    data = response.json()

    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws_id,
                AuditLog.action == "user.created",
            )
        )
        entries = result.scalars().all()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.object_type == "user"
        assert entry.meta["email"] == "auditme@orqion.local"
        assert "password" not in entry.meta
        # Password must not leak into audit meta
        assert data["password"] not in str(entry.meta)


@pytest.mark.asyncio
async def test_create_user_duplicate_email(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/users — duplicate email → 400."""
    await _login_as_admin(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "admin").limit(1)
        )
        admin_role = result.scalar_one()

    # First create succeeds
    response = await api_client.post(
        "/api/users",
        json={"email": "dup@orqion.local", "role_id": admin_role.id},
    )
    assert response.status_code == 200

    # Second create with same email → 400
    response = await api_client.post(
        "/api/users",
        json={"email": "dup@orqion.local", "role_id": admin_role.id},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_user_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/users — non-admin → 404."""
    await _login_as_role(api_client, app_fixture, "developer")
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "developer").limit(1)
        )
        dev_role = result.scalar_one()

    response = await api_client.post(
        "/api/users",
        json={"email": "nope@orqion.local", "role_id": dev_role.id},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_login_returns_must_change_password(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Login response includes must_change_password flag."""
    await _login_as_admin(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "admin").limit(1)
        )
        admin_role = result.scalar_one()

    response = await api_client.post(
        "/api/users",
        json={"email": "mustchange@orqion.local", "role_id": admin_role.id},
    )
    assert response.status_code == 200
    password = response.json()["password"]

    # Logout admin
    await api_client.post("/api/auth/logout")
    api_client.cookies.clear()

    # Login as new user
    login_resp = await api_client.post(
        "/api/auth/login",
        json={"email": "mustchange@orqion.local", "password": password},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert login_data["user"]["must_change_password"] is True


@pytest.mark.asyncio
async def test_change_password_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/auth/change-password — changes password, clears flag."""
    await _login_as_admin(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "admin").limit(1)
        )
        admin_role = result.scalar_one()

    # Create user
    response = await api_client.post(
        "/api/users",
        json={"email": "chgpass@orqion.local", "role_id": admin_role.id},
    )
    assert response.status_code == 200
    old_password = response.json()["password"]

    # Logout admin, login as new user
    await api_client.post("/api/auth/logout")
    api_client.cookies.clear()
    await api_client.post(
        "/api/auth/login",
        json={"email": "chgpass@orqion.local", "password": old_password},
    )

    # Change password
    new_password = "brand-new-secure-password-999"
    change_resp = await api_client.post(
        "/api/auth/change-password",
        json={"old_password": old_password, "new_password": new_password},
    )
    assert change_resp.status_code == 200
    assert change_resp.json()["status"] == "ok"

    # Verify must_change_password is cleared
    async with factory() as session:
        result = await session.execute(select(User).where(User.email == "chgpass@orqion.local"))
        user = result.scalar_one()
        assert user.must_change_password is False

    # Verify old password no longer works
    await api_client.post("/api/auth/logout")
    api_client.cookies.clear()
    old_login = await api_client.post(
        "/api/auth/login",
        json={"email": "chgpass@orqion.local", "password": old_password},
    )
    assert old_login.status_code == 401

    # Verify new password works
    new_login = await api_client.post(
        "/api/auth/login",
        json={"email": "chgpass@orqion.local", "password": new_password},
    )
    assert new_login.status_code == 200
    assert new_login.json()["user"]["must_change_password"] is False


@pytest.mark.asyncio
async def test_change_password_wrong_old(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/auth/change-password — wrong old password → 400."""
    await _login_as_admin(api_client, app_fixture)

    change_resp = await api_client.post(
        "/api/auth/change-password",
        json={"old_password": "wrong-password", "new_password": "new-pass-123"},
    )
    assert change_resp.status_code == 400


@pytest.mark.asyncio
async def test_change_password_same_as_old(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/auth/change-password — same password → 400."""
    await _login_as_admin(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "admin").limit(1)
        )
        admin_role = result.scalar_one()

    # Create user
    response = await api_client.post(
        "/api/users",
        json={"email": "samepass@orqion.local", "role_id": admin_role.id},
    )
    old_password = response.json()["password"]

    # Logout admin, login as new user
    await api_client.post("/api/auth/logout")
    api_client.cookies.clear()
    await api_client.post(
        "/api/auth/login",
        json={"email": "samepass@orqion.local", "password": old_password},
    )

    # Try same password
    change_resp = await api_client.post(
        "/api/auth/change-password",
        json={"old_password": old_password, "new_password": old_password},
    )
    assert change_resp.status_code == 400


@pytest.mark.asyncio
async def test_change_password_invalidates_other_sessions(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Change password invalidates all other sessions except current."""
    await _login_as_admin(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "admin").limit(1)
        )
        admin_role = result.scalar_one()

    # Create user
    response = await api_client.post(
        "/api/users",
        json={"email": "multisess@orqion.local", "role_id": admin_role.id},
    )
    old_password = response.json()["password"]
    user_id = response.json()["id"]

    # Create two sessions for the new user (simulating login from two devices)
    async with factory() as session:
        await create_session(session, user_id, ws_id, Settings())
        await create_session(session, user_id, ws_id, Settings())
        await session.commit()

    # Verify both sessions exist
    async with factory() as session:
        result = await session.execute(select(Session).where(Session.user_id == user_id))
        all_sessions = result.scalars().all()
        assert len(all_sessions) >= 2

    # Login as new user (creates current session)
    await api_client.post("/api/auth/logout")
    api_client.cookies.clear()
    await api_client.post(
        "/api/auth/login",
        json={"email": "multisess@orqion.local", "password": old_password},
    )

    # Change password — should invalidate all other sessions
    new_password = "completely-new-password-456"
    change_resp = await api_client.post(
        "/api/auth/change-password",
        json={"old_password": old_password, "new_password": new_password},
    )
    assert change_resp.status_code == 200

    # Verify only current session remains
    async with factory() as session:
        result = await session.execute(select(Session).where(Session.user_id == user_id))
        remaining = result.scalars().all()
        assert len(remaining) == 1
        current_cookie = api_client.cookies.get(COOKIE_NAME)
        assert remaining[0].id == current_cookie


@pytest.mark.asyncio
async def test_change_password_audit_log(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Change password writes user.password_changed to audit_log."""
    await _login_as_admin(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id

    async with factory() as session:
        result = await session.execute(
            select(Role).where(Role.workspace_id == ws_id, Role.name == "admin").limit(1)
        )
        admin_role = result.scalar_one()

    # Create user
    response = await api_client.post(
        "/api/users",
        json={"email": "auditpass@orqion.local", "role_id": admin_role.id},
    )
    old_password = response.json()["password"]
    user_id = response.json()["id"]

    # Logout admin, login as new user
    await api_client.post("/api/auth/logout")
    api_client.cookies.clear()
    await api_client.post(
        "/api/auth/login",
        json={"email": "auditpass@orqion.local", "password": old_password},
    )

    # Change password
    change_resp = await api_client.post(
        "/api/auth/change-password",
        json={"old_password": old_password, "new_password": "new-audit-pass-789"},
    )
    assert change_resp.status_code == 200

    # Verify audit_log
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws_id,
                AuditLog.action == "user.password_changed",
                AuditLog.object_id == user_id,
            )
        )
        entries = result.scalars().all()
        assert len(entries) == 1
        assert entries[0].actor_user_id == user_id
        # No password content in meta
        assert "password" not in entries[0].meta
