"""Тест API моделей: создание, обновление, отключённая модель не выдаётся."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> str:
    from app.auth.passwords import hash_password
    from app.auth.sessions import COOKIE_NAME, create_session
    from app.config import Settings
    from app.db.models import Role, User, Workspace
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
    return session_id


async def _create_provider(api_client: httpx.AsyncClient) -> str:
    response = await api_client.post(
        "/api/providers",
        json={"kind": "ollama", "base_url": "http://localhost:11434"},
    )
    provider_id: str = response.json()["id"]
    return provider_id


@pytest.mark.asyncio
async def test_create_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_as_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client)

    response = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={
            "provider_id": provider_id,
            "alias": "local/qwen3-8b",
            "upstream_name": "qwen2.5-coder-7b-instruct",
            "locality": "local",
            "max_input_tokens": 32000,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["alias"] == "local/qwen3-8b"
    assert body["upstream_name"] == "qwen2.5-coder-7b-instruct"
    assert body["locality"] == "local"
    assert body["max_input_tokens"] == 32000
    assert body["enabled"] is True


@pytest.mark.asyncio
async def test_model_alias_unique_per_workspace(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_as_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client)

    await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"provider_id": provider_id, "alias": "local/dup", "upstream_name": "m1"},
    )

    response = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"provider_id": provider_id, "alias": "local/dup", "upstream_name": "m2"},
    )
    assert response.status_code in (400, 409, 500)


@pytest.mark.asyncio
async def test_disabled_model_not_in_provider_list(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Отключённая модель не выдаётся в списке провайдера."""
    await _login_as_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client)

    create_resp = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"provider_id": provider_id, "alias": "local/m1", "upstream_name": "m1"},
    )
    model_id = create_resp.json()["id"]

    await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"provider_id": provider_id, "alias": "local/m2", "upstream_name": "m2"},
    )

    await api_client.patch(
        f"/api/providers/models/{model_id}",
        json={"enabled": False},
    )

    response = await api_client.get("/api/providers")
    providers = response.json()["providers"]
    provider = next(p for p in providers if p["id"] == provider_id)
    aliases = [m["alias"] for m in provider["models"]]
    assert "local/m1" not in aliases
    assert "local/m2" in aliases


@pytest.mark.asyncio
async def test_update_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_as_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client)

    create_resp = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"provider_id": provider_id, "alias": "local/m1", "upstream_name": "m1"},
    )
    model_id = create_resp.json()["id"]

    response = await api_client.patch(
        f"/api/providers/models/{model_id}",
        json={"max_input_tokens": 64000, "cost_in": 0.5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["max_input_tokens"] == 64000
    assert body["cost_in"] == 0.5
