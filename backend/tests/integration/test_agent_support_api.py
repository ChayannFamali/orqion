"""Т-502 (срез 2): фундамент агентного модуля в управляющем API.

Решение 3 пересмотренного дизайн-ревью: пригодность модели к
инструментам — ручной флаг ``supports_tools`` по образцу флагов
рассуждения (Т-113/Т-445), администратор ставит сам. Решение 10:
режим разговора ``mode`` ("chat"/"agent") — обычный чат поведение не
меняет, существующие разговоры получают "chat" по умолчанию.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
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

        user = User(
            workspace_id=ws.id,
            email="admin@orqion.local",
            password_hash=hash_password("admin-password-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


async def _create_provider(api_client: httpx.AsyncClient) -> str:
    response = await api_client.post(
        "/api/providers",
        json={"kind": "ollama", "base_url": "http://localhost:11434"},
    )
    provider_id: str = response.json()["id"]
    return provider_id


@pytest.mark.asyncio
async def test_supports_tools_default_false(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Модель без флага создаётся с supports_tools=False."""
    await _login_as_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client)

    response = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"alias": "local/m1", "upstream_name": "m1"},
    )
    assert response.status_code == 201
    assert response.json()["supports_tools"] is False


@pytest.mark.asyncio
async def test_supports_tools_create_and_update(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Флаг ставится при создании, меняется через PATCH, виден в списке."""
    await _login_as_admin(api_client, app_fixture)
    provider_id = await _create_provider(api_client)

    create_resp = await api_client.post(
        f"/api/providers/{provider_id}/models",
        json={"alias": "local/agent", "upstream_name": "agent", "supports_tools": True},
    )
    assert create_resp.status_code == 201
    model_id = create_resp.json()["id"]
    assert create_resp.json()["supports_tools"] is True

    # Видна в пользовательском списке моделей (точка входа в агентный
    # диалог фильтрует по этому полю).
    models_resp = await api_client.get("/api/models")
    assert models_resp.status_code == 200
    aliases = {m["alias"]: m for m in models_resp.json()}
    assert aliases["local/agent"]["supports_tools"] is True

    # Снятие флага через управляющий API.
    patch_resp = await api_client.patch(
        f"/api/providers/models/{model_id}",
        json={"supports_tools": False},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["supports_tools"] is False


@pytest.mark.asyncio
async def test_conversation_mode_default_chat(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Существующий путь создания разговора даёт режим "chat"."""
    await _login_as_admin(api_client, app_fixture)

    create_resp = await api_client.post("/api/conversations", json={"title": "обычный"})
    assert create_resp.status_code == 201
    assert create_resp.json()["mode"] == "chat"

    list_resp = await api_client.get("/api/conversations")
    items = list_resp.json()["conversations"]
    assert all(item["mode"] == "chat" for item in items)
