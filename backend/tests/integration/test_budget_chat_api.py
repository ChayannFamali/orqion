"""Integration-тест: budget enforcement через /api/chat.

Проверяет проводку enforce_all в prepare_chat: заполненный usage_daily
близко к лимиту → /api/chat возвращает 429 BudgetExceeded.

Провайдер подменяется заглушкой — обращений к сети нет (AGENTS.md §12.2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, UsageDaily, User
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from fastapi import FastAPI


async def _login_with_budget(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    tokens_month: int,
) -> str:
    """Создаёт пользователя с бюджетом tokens_month и возвращает session_id."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        policy = BUILTIN_ROLES["developer"].model_dump()
        policy["budget"] = {"tokens_month": tokens_month}

        role = Role(
            workspace_id=workspace_id,
            name="budget-test",
            is_builtin=False,
            policy=policy,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="budget@orqion.local",
            password_hash=hash_password("budget-pass-123"),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_provider_and_model(app_fixture: FastAPI) -> str:
    """Создаёт провайдера и модель с известной стоимостью."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", app_fixture.state.secret_key),
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias="local/budget-test-model",
            upstream_name="budget-test-model",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(model)
        await session.commit()
        return model.id


async def _seed_usage_near_limit(
    app_fixture: FastAPI,
    user_id: str,
    model_id: str,
    tokens_used: int,
) -> None:
    """Заполняет usage_daily близко к лимиту."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    today = datetime.now(tz=UTC).date().isoformat()
    async with factory() as session:
        session.add(
            UsageDaily(
                workspace_id=workspace_id,
                date=today,
                user_id=user_id,
                model_id=model_id,
                requests=50,
                tokens_in=tokens_used,
                tokens_out=0,
                cost=0.0,
                errors=0,
            )
        )
        await session.commit()


def _patch_provider_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменяет ProviderClient — заглушка без обращения к сети."""

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        yield {"type": "token", "v": "Hello"}

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)
    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)


@pytest.mark.asyncio
async def test_budget_enforced_through_chat_api(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Бюджет близок к лимиту → /api/chat возвращает 429."""
    model_id = await _seed_provider_and_model(app_fixture)
    user_id = await _login_with_budget(api_client, app_fixture, tokens_month=10_000)

    # Заполняем usage_daily почти до лимита
    await _seed_usage_near_limit(app_fixture, user_id, model_id, tokens_used=9_990)

    _patch_provider_client(monkeypatch)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Say hello"}],
            "model_alias": "local/budget-test-model",
            "stream": False,
        },
    )

    assert response.status_code == 429
    body = response.json()
    assert body["error"] == "budget_exceeded"
    constraint = body["constraint"]
    assert constraint is not None
    assert constraint["type"] == "tokens_month"
    assert constraint["limit"] == 10_000
    assert constraint["used"] == 9_990
