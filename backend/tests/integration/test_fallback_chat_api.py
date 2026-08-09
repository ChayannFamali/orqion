"""Тесты fallback при ошибке провайдера (T-116b).

Проверки:
- основная модель 5xx → fallback → успех → usage_event записан с fallback-моделью
- support role: fallback не уходит на external (policy.models)
- ошибка после первого токена → error event, без fallback
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, RoutingRule, User
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from fastapi import FastAPI
from sqlalchemy import select


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

        user = User(
            workspace_id=workspace_id,
            email=f"{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
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
    alias: str,
    upstream: str,
    locality: str = "local",
    enabled: bool = True,
) -> tuple[str, str]:
    """Создаёт провайдера и модель. Возвращает (model_id, provider_id)."""
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
            alias=alias,
            upstream_name=upstream,
            locality=locality,
            max_input_tokens=32000,
            enabled=enabled,
        )
        session.add(model)
        await session.commit()
        return model.id, provider.id


async def _seed_routing_rule_with_fallback(
    app_fixture: FastAPI,
    primary_alias: str,
    fallback_alias: str,
) -> None:
    """Создаёт routing rule: to=[primary], fallback=[fallback]."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        rule = RoutingRule(
            workspace_id=workspace_id,
            order=1,
            is_default=False,
            is_terminal=True,
            when_corpus_class=None,
            when_role=None,
            when_task=None,
            when_model_alias=None,
            to_models=[primary_alias],
            allow_locality=None,
            fallback_models=[fallback_alias],
            reason="test-fallback-rule",
        )
        session.add(rule)
        # Удаляем default rule, чтобы наша сработала
        result = await session.execute(
            select(RoutingRule).where(
                RoutingRule.workspace_id == workspace_id,
                RoutingRule.is_default.is_(True),
            )
        )
        for r in result.scalars().all():
            await session.delete(r)
        await session.commit()


@pytest.mark.asyncio
async def test_fallback_on_primary_5xx_non_stream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Основная модель 5xx → fallback → успех → usage_event записан с fallback-моделью."""
    await _login_with_role(api_client, app_fixture, "admin")
    primary_id, _ = await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream")
    fallback_id, _ = await _seed_provider_and_model(app_fixture, "local/fallback", "fallback-upstream")
    await _seed_routing_rule_with_fallback(app_fixture, "local/primary", "local/fallback")

    call_count = {"primary": 0, "fallback": 0}

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        if model == "primary-upstream":
            call_count["primary"] += 1
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        call_count["fallback"] += 1
        return {
            "choices": [{"message": {"content": "Fallback response"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "complete"
    assert body["content"] == "Fallback response"
    assert body["model"] == "local/fallback"

    assert call_count["primary"] >= 1
    assert call_count["fallback"] == 1

    # Проверяем, что usage_event записан с fallback-моделью
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    from app.db.models import UsageEvent

    async with factory() as session:
        result = await session.execute(
            select(UsageEvent).where(UsageEvent.workspace_id == workspace_id)
        )
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].model_id == fallback_id
        assert events[0].model_id != primary_id


@pytest.mark.asyncio
async def test_fallback_on_primary_5xx_stream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stream: основная модель 5xx до первого токена → fallback → стриминг успешно."""
    await _login_with_role(api_client, app_fixture, "admin")
    await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream")
    fallback_id, _ = await _seed_provider_and_model(app_fixture, "local/fallback", "fallback-upstream")
    await _seed_routing_rule_with_fallback(app_fixture, "local/primary", "local/fallback")

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        if model == "primary-upstream":
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        yield "Fallback"
        yield " "
        yield "token"

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    lines = response.text.strip().split("\n")
    token_events = [
        json.loads(l[6:]) for l in lines if l.startswith("data: ") and "[DONE]" not in l
    ]
    token_events = [e for e in token_events if e["type"] == "token"]
    assert len(token_events) == 3
    assert "".join(e["v"] for e in token_events) == "Fallback token"

    # Проверяем, что usage_event записан с fallback-моделью
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    from app.db.models import UsageEvent

    async with factory() as session:
        result = await session.execute(
            select(UsageEvent).where(UsageEvent.workspace_id == workspace_id)
        )
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].model_id == fallback_id


@pytest.mark.asyncio
async def test_no_fallback_after_first_token(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ошибка после первого токена → error event, без fallback."""
    await _login_with_role(api_client, app_fixture, "admin")
    await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream")
    await _seed_provider_and_model(app_fixture, "local/fallback", "fallback-upstream")
    await _seed_routing_rule_with_fallback(app_fixture, "local/primary", "local/fallback")

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        if model == "primary-upstream":
            yield "First"
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        # Fallback не должен вызываться
        yield "Should not see this"

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    lines = response.text.strip().split("\n")
    events = [
        json.loads(l[6:]) for l in lines if l.startswith("data: ") and "[DONE]" not in l
    ]
    token_events = [e for e in events if e["type"] == "token"]
    error_events = [e for e in events if e["type"] == "error"]

    # Был один токен, затем ошибка — fallback не применился
    assert len(token_events) == 1
    assert token_events[0]["v"] == "First"
    assert len(error_events) == 1
    assert error_events[0]["code"] == "provider_unavailable"


@pytest.mark.asyncio
async def test_support_role_fallback_stays_local(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Support role (models=["local/*"]): fallback не уходит на external."""
    support_policy = BUILTIN_ROLES["support"].model_dump()
    await _login_with_role(api_client, app_fixture, "support", policy=support_policy)

    await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream", "local")
    await _seed_provider_and_model(app_fixture, "external/fallback", "external-upstream", "external")
    await _seed_routing_rule_with_fallback(app_fixture, "local/primary", "external/fallback")

    fallback_called = {"yes": False}

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        if model == "primary-upstream":
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        fallback_called["yes"] = True
        return {
            "choices": [{"message": {"content": "External"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    # Support не может использовать external-модель: fallback отфильтрован policy.models
    # Primary падает, fallback недоступен → ошибка
    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )

    # Маршрутизатор: fallback external/fallback отфильтрован через policy.models
    # Primary падает, fallback недоступен → ошибка
    body = response.json()
    assert body["type"] == "error"
    assert fallback_called["yes"] is False
