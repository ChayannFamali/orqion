"""Тесты учёта потребления: usage_event записывается при успехе, ошибке, обрыве.

Проверки:
- usage_event существует после чат-запроса
- содержимое запросов/ответов отсутствует в usage_event
- стоимость считается по cost_in/cost_out
- запись при ошибке провайдера (status=error, error_code)
- FK nullable: conversation_id/message_id могут быть NULL
- calculate_cost: корректный расчёт
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, UsageEvent, User
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from app.usage.service import calculate_cost
from fastapi import FastAPI
from sqlalchemy import select


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="admin@orqion.local",
            password_hash=hash_password("admin-password-123"),
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
    cost_in: float | None = None,
    cost_out: float | None = None,
) -> str:
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
            alias="local/test-model",
            upstream_name="test-model",
            locality="local",
            max_input_tokens=32000,
            cost_in=cost_in,
            cost_out=cost_out,
            enabled=True,
        )
        session.add(model)
        await session.commit()
        return model.id


def _patch_provider_client(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        for word in response.split():
            yield word + " "

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": response}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)
    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)


async def _get_usage_events(
    app_fixture: FastAPI,
) -> list[UsageEvent]:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        result = await session.execute(
            select(UsageEvent).where(UsageEvent.workspace_id == workspace_id)
        )
        return list(result.scalars().all())


# --- calculate_cost unit tests ---


def test_calculate_cost_with_rates() -> None:
    """cost_in=2.0/1M, cost_out=6.0/1M → 1000 in + 500 out = 0.002 + 0.003 = 0.005."""
    cost = calculate_cost(1000, 500, 2.0, 6.0)
    assert cost is not None
    assert abs(cost - 0.005) < 0.0001


def test_calculate_cost_no_rates() -> None:
    """Оба rate None → cost None."""
    assert calculate_cost(1000, 500, None, None) is None


def test_calculate_cost_only_in() -> None:
    """Только cost_in → cost_out = 0."""
    cost = calculate_cost(1000, 500, 2.0, None)
    assert cost is not None
    assert abs(cost - 0.002) < 0.0001


def test_calculate_cost_zero_tokens() -> None:
    """0 токенов → cost = 0.0 (не None)."""
    cost = calculate_cost(0, 0, 2.0, 6.0)
    assert cost == 0.0


# --- Integration tests ---


@pytest.mark.asyncio
async def test_usage_event_created_after_non_stream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-stream чат → usage_event записан."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture, cost_in=2.0, cost_out=6.0)
    _patch_provider_client(monkeypatch, "hello")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    event = events[0]
    assert event.status == "ok"
    assert event.error_code is None
    assert event.tokens_in is not None and event.tokens_in > 0
    assert event.tokens_out is not None and event.tokens_out > 0
    assert event.cost is not None and event.cost > 0
    assert event.latency_ms is not None and event.latency_ms >= 0


@pytest.mark.asyncio
async def test_usage_event_created_after_stream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stream чат → usage_event записан."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture, cost_in=1.0, cost_out=3.0)
    _patch_provider_client(monkeypatch, "hello world")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
    )

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    assert events[0].status == "ok"


@pytest.mark.asyncio
async def test_usage_event_on_provider_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ошибка провайдера → usage_event со status=error и error_code."""

    async def _error_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        yield "partial "
        raise RuntimeError("provider crashed")

    monkeypatch.setattr(ProviderClient, "stream", _error_stream)

    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
    )

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    event = events[0]
    assert event.status == "error"
    assert event.error_code is not None


@pytest.mark.asyncio
async def test_usage_event_no_content(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """usage_event не содержит содержимого запросов и ответов (AGENTS.md §5.2)."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "secret response")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "secret prompt"}], "stream": False},
    )

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    event = events[0]

    # Проверяем, что ни одно поле не содержит содержимого
    event_dict = event.__dict__
    for key, value in event_dict.items():
        if isinstance(value, str):
            assert "secret" not in value.lower(), f"Content leaked in field '{key}': {value}"


@pytest.mark.asyncio
async def test_usage_event_cost_calculated(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cost в usage_event = calculate_cost(tokens_in, tokens_out, cost_in, cost_out)."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture, cost_in=2.0, cost_out=6.0)
    _patch_provider_client(monkeypatch, "ok")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    event = events[0]
    assert event.cost is not None
    expected = calculate_cost(event.tokens_in, event.tokens_out, 2.0, 6.0)
    assert expected is not None
    assert abs(event.cost - expected) < 0.0001


@pytest.mark.asyncio
async def test_usage_event_cost_null_when_no_rates(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без cost_in/cost_out → cost = None в usage_event."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture, cost_in=None, cost_out=None)
    _patch_provider_client(monkeypatch, "ok")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    assert events[0].cost is None


@pytest.mark.asyncio
async def test_usage_event_conversation_id_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """conversation_id в usage_event указывает на созданный диалог."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "ok")

    resp = await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    conv_id = resp.json().get("conversation_id")

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    assert events[0].conversation_id == conv_id


@pytest.mark.asyncio
async def test_usage_event_model_id_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """model_id в usage_event указывает на модель."""
    await _login_as_admin(api_client, app_fixture)
    model_id = await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "ok")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    assert events[0].model_id == model_id


@pytest.mark.asyncio
async def test_usage_event_non_stream_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-stream ошибка → usage_event со status=error."""

    async def _error_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        raise RuntimeError("provider crashed")

    monkeypatch.setattr(ProviderClient, "complete", _error_complete)

    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    assert events[0].status == "error"
    assert events[0].error_code is not None


@pytest.mark.asyncio
async def test_usage_event_message_id_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """message_id в usage_event указывает на ассистент-сообщение."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "ok reply")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    events = await _get_usage_events(app_fixture)
    assert len(events) == 1
    assert events[0].message_id is not None

    # Проверяем, что message_id указывает на реальное ассистент-сообщение
    from app.db.models import Message

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        msg = await session.get(Message, events[0].message_id)
        assert msg is not None
        assert msg.role == "assistant"
