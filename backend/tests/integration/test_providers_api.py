"""Тест providers API: создание, список, обновление, ключ не возвращается."""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import decrypt_api_key
from app.db.models import Role, User, Workspace
from fastapi import FastAPI
from sqlalchemy import select


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Создаёт admin-пользователя и логинится через cookie."""
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        ws = Workspace(name="test")
        session.add(ws)
        await session.flush()

        role = Role(
            workspace_id=ws.id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()

        password = "admin-password-123"
        user = User(
            workspace_id=ws.id,
            email="admin@orqion.local",
            password_hash=hash_password(password),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


@pytest.mark.asyncio
async def test_create_provider_key_encrypted_not_returned(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """API-ключ шифруется при записи и не возвращается в ответе."""
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.post(
        "/api/providers",
        json={
            "kind": "openai",
            "base_url": "http://localhost:1234/v1",
            "api_key": "sk-secret-key-123",
            "enabled": True,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "openai"
    assert body["base_url"] == "http://localhost:1234/v1"
    assert "api_key" not in body
    assert "api_key_enc" not in body

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from app.db.models import Provider

        result = await session.execute(select(Provider))
        provider = result.scalar_one()
        assert provider.api_key_enc is not None
        assert provider.api_key_enc != "sk-secret-key-123"
        secret_key = app_fixture.state.secret_key
        assert decrypt_api_key(provider.api_key_enc, secret_key) == "sk-secret-key-123"


@pytest.mark.asyncio
async def test_create_provider_without_key(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Провайдер без API-ключа (локальный) — api_key_enc=None."""
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.post(
        "/api/providers",
        json={
            "kind": "ollama",
            "base_url": "http://localhost:11434",
            "enabled": True,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "ollama"
    assert "api_key" not in body


@pytest.mark.asyncio
async def test_list_providers(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_as_admin(api_client, app_fixture)

    await api_client.post(
        "/api/providers",
        json={"kind": "ollama", "base_url": "http://localhost:11434"},
    )
    await api_client.post(
        "/api/providers",
        json={"kind": "lmstudio", "base_url": "http://localhost:1234/v1"},
    )

    response = await api_client.get("/api/providers")
    assert response.status_code == 200
    body = response.json()
    assert len(body["providers"]) == 2


@pytest.mark.asyncio
async def test_update_provider_key_replaced(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """При обновлении ключа старый шифр заменяется новым."""
    await _login_as_admin(api_client, app_fixture)

    create_response = await api_client.post(
        "/api/providers",
        json={
            "kind": "openai",
            "base_url": "http://localhost:1234/v1",
            "api_key": "sk-old-key",
        },
    )
    provider_id = create_response.json()["id"]

    await api_client.patch(
        f"/api/providers/{provider_id}",
        json={"api_key": "sk-new-key"},
    )

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        from app.db.models import Provider

        result = await session.execute(select(Provider).where(Provider.id == provider_id))
        provider = result.scalar_one()
        secret_key = app_fixture.state.secret_key
        assert decrypt_api_key(provider.api_key_enc, secret_key) == "sk-new-key"


@pytest.mark.asyncio
async def test_update_provider_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.patch(
        "/api/providers/nonexistent",
        json={"enabled": False},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_providers_require_auth(api_client: httpx.AsyncClient) -> None:
    """GET /api/providers без cookie → 401."""
    response = await api_client.get("/api/providers")
    assert response.status_code == 401
