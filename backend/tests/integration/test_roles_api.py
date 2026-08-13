"""Тест roles API: список, детали, создание, обновление политики, access control, audit."""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import AuditLog, Role, User
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
) -> None:
    """Логинит пользователя с заданной ролью (non-admin)."""
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
            email=f"roles-{role_name}{email_suffix}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_roles_returns_builtin_roles(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/roles → список из 5 builtin-ролей."""
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.get("/api/roles")
    assert resp.status_code == 200
    roles = resp.json()["roles"]
    names = [r["name"] for r in roles]
    assert "support" in names
    assert "developer" in names
    assert "architect" in names
    assert "manager" in names
    assert "admin" in names

    admin_role = next(r for r in roles if r["name"] == "admin")
    assert admin_role["is_builtin"] is True
    assert admin_role["policy"]["capabilities"] == ["*"]


@pytest.mark.asyncio
async def test_list_roles_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/roles → 404 для non-admin."""
    await _login_as_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/roles")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_role_detail(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/roles/{id} → полная политика роли."""
    await _login_as_admin(api_client, app_fixture)

    list_resp = await api_client.get("/api/roles")
    roles = list_resp.json()["roles"]
    support_id = next(r["id"] for r in roles if r["name"] == "support")

    resp = await api_client.get(f"/api/roles/{support_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "support"
    assert data["is_builtin"] is True
    assert data["policy"]["models"] == ["local/*"]
    assert data["policy"]["capabilities"] == ["chat"]


@pytest.mark.asyncio
async def test_get_role_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/roles/{id} с несуществующим ID → 404."""
    await _login_as_admin(api_client, app_fixture)

    resp = await api_client.get("/api/roles/nonexistent-id")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_custom_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/roles → создаёт кастомную роль с is_builtin=False."""
    await _login_as_admin(api_client, app_fixture)

    policy = {
        "models": ["local/*"],
        "max_input_tokens": 8000,
        "max_output_tokens": 2000,
        "reasoning": "off",
        "budget": {"tokens_month": 500000, "cost_month": 0},
        "rpm": 20,
        "tpm": 10000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.post(
        "/api/roles",
        json={"name": "intern", "policy": policy},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "intern"
    assert data["is_builtin"] is False
    assert data["policy"]["max_input_tokens"] == 8000


@pytest.mark.asyncio
async def test_create_role_is_builtin_always_false(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """is_builtin в теле игнорируется — кастомная роль всегда is_builtin=False."""
    await _login_as_admin(api_client, app_fixture)

    policy = {
        "models": ["local/*"],
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "reasoning": "off",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.post(
        "/api/roles",
        json={"name": "custom-role", "policy": policy, "is_builtin": True},
    )
    assert resp.status_code == 201
    assert resp.json()["is_builtin"] is False


@pytest.mark.asyncio
async def test_create_role_duplicate_name_400(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST с дублем имени → 400, не 500."""
    await _login_as_admin(api_client, app_fixture)

    policy = {
        "models": ["local/*"],
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "reasoning": "off",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.post("/api/roles", json={"name": "intern", "policy": policy})
    assert resp.status_code == 201

    resp = await api_client.post("/api/roles", json={"name": "intern", "policy": policy})
    assert resp.status_code == 400
    assert resp.json()["reason"]


@pytest.mark.asyncio
async def test_create_role_invalid_policy_field_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST с некорректной политикой → 400 с указанием поля, не 500."""
    await _login_as_admin(api_client, app_fixture)

    # reasoning имеет недопустимое значение
    policy = {
        "models": ["local/*"],
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "reasoning": "always",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.post("/api/roles", json={"name": "bad", "policy": policy})
    assert resp.status_code == 400
    data = resp.json()
    assert "reasoning" in data["reason"] or "reasoning" in (data.get("hint") or "")


@pytest.mark.asyncio
async def test_create_role_unknown_field_rejected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST с неизвестным полем в policy → 400 (extra='forbid')."""
    await _login_as_admin(api_client, app_fixture)

    policy = {
        "models": ["local/*"],
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "reasoning": "off",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["chat"],
        "unknown_field": "should_fail",
    }
    resp = await api_client.post("/api/roles", json={"name": "bad2", "policy": policy})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_role_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/roles → 404 для non-admin."""
    await _login_as_role(api_client, app_fixture, "developer")

    policy = {
        "models": ["local/*"],
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "reasoning": "off",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.post("/api/roles", json={"name": "test", "policy": policy})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_role_policy(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH /api/roles/{id} → обновляет политику builtin-роли."""
    await _login_as_admin(api_client, app_fixture)

    list_resp = await api_client.get("/api/roles")
    roles = list_resp.json()["roles"]
    support_id = next(r["id"] for r in roles if r["name"] == "support")

    new_policy = {
        "models": ["local/*"],
        "max_input_tokens": 32000,
        "max_output_tokens": 4000,
        "reasoning": "optional",
        "budget": {"tokens_month": 3000000, "cost_month": 0},
        "rpm": 60,
        "tpm": 40000,
        "corpora": ["public", "team"],
        "capabilities": ["chat", "upload"],
    }
    resp = await api_client.patch(
        f"/api/roles/{support_id}",
        json={"policy": new_policy},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["policy"]["max_input_tokens"] == 32000
    assert data["policy"]["reasoning"] == "optional"
    assert data["policy"]["capabilities"] == ["chat", "upload"]


@pytest.mark.asyncio
async def test_update_role_invalid_policy_field_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH с некорректной политикой → 400 с указанием поля."""
    await _login_as_admin(api_client, app_fixture)

    list_resp = await api_client.get("/api/roles")
    roles = list_resp.json()["roles"]
    support_id = next(r["id"] for r in roles if r["name"] == "support")

    # negative max_input_tokens
    policy = {
        "models": ["local/*"],
        "max_input_tokens": -100,
        "max_output_tokens": 1000,
        "reasoning": "off",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.patch(
        f"/api/roles/{support_id}",
        json={"policy": policy},
    )
    assert resp.status_code == 400
    data = resp.json()
    # Pydantic ValidationError → 400 с указанием поля в reason или hint
    assert data["hint"]  # непустой hint с описанием ошибки валидации


@pytest.mark.asyncio
async def test_update_role_wildcard_combined_rejected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH с '*' + другие значения в capabilities → 400."""
    await _login_as_admin(api_client, app_fixture)

    list_resp = await api_client.get("/api/roles")
    roles = list_resp.json()["roles"]
    support_id = next(r["id"] for r in roles if r["name"] == "support")

    policy = {
        "models": ["local/*"],
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "reasoning": "off",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["*", "chat"],
    }
    resp = await api_client.patch(
        f"/api/roles/{support_id}",
        json={"policy": policy},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_role_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH /api/roles/{id} с несуществующим ID → 404."""
    await _login_as_admin(api_client, app_fixture)

    policy = {
        "models": ["local/*"],
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "reasoning": "off",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.patch(
        "/api/roles/nonexistent-id",
        json={"policy": policy},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_role_non_admin_forbidden(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH /api/roles/{id} → 404 для non-admin."""
    await _login_as_admin(api_client, app_fixture)

    list_resp = await api_client.get("/api/roles")
    roles = list_resp.json()["roles"]
    support_id = next(r["id"] for r in roles if r["name"] == "support")

    await _login_as_role(api_client, app_fixture, "developer")

    policy = {
        "models": ["local/*"],
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "reasoning": "off",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.patch(
        f"/api/roles/{support_id}",
        json={"policy": policy},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_role_writes_audit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """POST /api/roles → запись в audit_log с action='role.created'."""
    user_id = await _login_as_admin(api_client, app_fixture)

    policy = {
        "models": ["local/*"],
        "max_input_tokens": 4000,
        "max_output_tokens": 1000,
        "reasoning": "off",
        "budget": None,
        "rpm": 10,
        "tpm": 5000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.post("/api/roles", json={"name": "audited", "policy": policy})
    assert resp.status_code == 201
    role_id = resp.json()["id"]

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == "role.created",
                AuditLog.object_id == role_id,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.actor_user_id == user_id
        assert audit.object_type == "role"
        assert audit.meta["name"] == "audited"


@pytest.mark.asyncio
async def test_update_role_writes_audit_with_diff(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH /api/roles/{id} → audit_log с action='role.policy_changed', old+new в meta."""
    user_id = await _login_as_admin(api_client, app_fixture)

    list_resp = await api_client.get("/api/roles")
    roles = list_resp.json()["roles"]
    support_id = next(r["id"] for r in roles if r["name"] == "support")

    new_policy = {
        "models": ["local/*"],
        "max_input_tokens": 99999,
        "max_output_tokens": 2000,
        "reasoning": "off",
        "budget": None,
        "rpm": 30,
        "tpm": 20000,
        "corpora": ["public"],
        "capabilities": ["chat"],
    }
    resp = await api_client.patch(
        f"/api/roles/{support_id}",
        json={"policy": new_policy},
    )
    assert resp.status_code == 200

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == "role.policy_changed",
                AuditLog.object_id == support_id,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.actor_user_id == user_id
        assert audit.object_type == "role"
        assert "old" in audit.meta
        assert "new" in audit.meta
        assert audit.meta["old"]["max_input_tokens"] == 16000
        assert audit.meta["new"]["max_input_tokens"] == 99999
