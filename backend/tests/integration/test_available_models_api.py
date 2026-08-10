"""Тест GET /api/models — фильтр по policy.models и enabled.

Проверки:
- admin видит все enabled-модели
- support (models=["local/*"]) не видит external-модели
- disabled-модели не видны
- disabled-провайдер скрывает его модели
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI


async def _login_with_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str = "admin",
    policy: dict[str, Any] | None = None,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role_policy = policy or BUILTIN_ROLES[role_name].model_dump()
        role = Role(
            workspace_id=workspace_id,
            name=role_name,
            is_builtin=True,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()

        password = "admin-password-123"
        user = User(
            workspace_id=workspace_id,
            email=f"{role_name}@orqion.local",
            password_hash=hash_password(password),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_provider_and_model(
    app_fixture: FastAPI,
    model_alias: str = "local/test-model",
    upstream_name: str = "test-model",
    locality: str = "local",
    model_enabled: bool = True,
    provider_enabled: bool = True,
) -> str:
    """Создаёт провайдера и модель. Возвращает model_id."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", app_fixture.state.secret_key),
            enabled=provider_enabled,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias=model_alias,
            upstream_name=upstream_name,
            locality=locality,
            enabled=model_enabled,
        )
        session.add(model)
        await session.commit()
        return model.id


@pytest.mark.asyncio
async def test_admin_sees_all_enabled_models(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """admin (models=["*"]) видит все enabled-модели."""
    await _login_with_role(api_client, app_fixture, "admin")
    await _seed_provider_and_model(app_fixture, "local/qwen-7b", "qwen-7b", "local")
    await _seed_provider_and_model(app_fixture, "external/gpt-4", "gpt-4", "external")

    response = await api_client.get("/api/models")
    assert response.status_code == 200
    models = response.json()
    aliases = [m["alias"] for m in models]
    assert "local/qwen-7b" in aliases
    assert "external/gpt-4" in aliases


@pytest.mark.asyncio
async def test_support_does_not_see_external_models(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """support (models=["local/*"]) не видит external-модели."""
    support_policy = BUILTIN_ROLES["support"].model_dump()
    await _login_with_role(api_client, app_fixture, "support", policy=support_policy)
    await _seed_provider_and_model(app_fixture, "local/qwen-7b", "qwen-7b", "local")
    await _seed_provider_and_model(app_fixture, "external/gpt-4", "gpt-4", "external")

    response = await api_client.get("/api/models")
    assert response.status_code == 200
    models = response.json()
    aliases = [m["alias"] for m in models]
    assert "local/qwen-7b" in aliases
    assert "external/gpt-4" not in aliases


@pytest.mark.asyncio
async def test_disabled_model_not_listed(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """disabled-модель не видна через GET /api/models."""
    await _login_with_role(api_client, app_fixture, "admin")
    await _seed_provider_and_model(app_fixture, "local/enabled", "m1", "local", model_enabled=True)
    await _seed_provider_and_model(
        app_fixture, "local/disabled", "m2", "local", model_enabled=False
    )

    response = await api_client.get("/api/models")
    assert response.status_code == 200
    models = response.json()
    aliases = [m["alias"] for m in models]
    assert "local/enabled" in aliases
    assert "local/disabled" not in aliases


@pytest.mark.asyncio
async def test_disabled_provider_hides_models(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Модели disabled-провайдера не видны через GET /api/models."""
    await _login_with_role(api_client, app_fixture, "admin")
    await _seed_provider_and_model(
        app_fixture, "local/visible", "m1", "local", provider_enabled=True
    )
    await _seed_provider_and_model(
        app_fixture, "local/hidden", "m2", "local", provider_enabled=False
    )

    response = await api_client.get("/api/models")
    assert response.status_code == 200
    models = response.json()
    aliases = [m["alias"] for m in models]
    assert "local/visible" in aliases
    assert "local/hidden" not in aliases


@pytest.mark.asyncio
async def test_models_endpoint_requires_auth(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/models без аутентификации → 401."""
    response = await api_client.get("/api/models")
    assert response.status_code == 401
