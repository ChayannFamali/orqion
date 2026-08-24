"""T-440: reasoning-трейс модели в чате.

Решения дизайн-ревью: В1 — отдельный тип события в стриме и отдельное
поле reasoning_content в non-streaming ответе; Г1 — только
OpenAI-совместимый reasoning_content; трейс сохраняется в meta
ассистента (иначе теряется при перезагрузке). Биллинг не затрагивается.

Провайдер подменяется заглушкой — обращения к сети запрещены (AGENTS.md §12.2).
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
from app.db.models import Model, Provider, Role, User
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from fastapi import FastAPI

REASONING_TEXT = "Let me think step by step."
ANSWER_TEXT = "The answer is 42."


async def _login_as_admin(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> str:
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


async def _seed_provider_and_model(app_fixture: FastAPI) -> str:
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
            alias="local/reasoner",
            upstream_name="reasoner",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(model)
        await session.commit()
        return model.id


def _patch_stream_with_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Стрим: сначала reasoning-события, затем токены ответа."""

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        yield {"type": "reasoning", "v": "Let me think "}
        yield {"type": "reasoning", "v": "step by step."}
        yield {"type": "token", "v": "The answer "}
        yield {"type": "token", "v": "is 42."}

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)


def _patch_complete_with_reasoning(
    monkeypatch: pytest.MonkeyPatch,
    reasoning: str | None,
) -> None:
    """Non-streaming: ответ с (опц.) полем reasoning_content."""

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"content": ANSWER_TEXT}
        if reasoning is not None:
            message["reasoning_content"] = reasoning
        return {
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)


async def _get_saved_messages(api_client: httpx.AsyncClient) -> list[dict[str, Any]]:
    convs = await api_client.get("/api/conversations")
    assert convs.json()["total"] == 1
    conv_id = convs.json()["conversations"][0]["id"]
    conv = await api_client.get(f"/api/conversations/{conv_id}")
    messages: list[dict[str, Any]] = conv.json()["messages"]
    return messages


def _parse_sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    return [
        json.loads(line[6:])
        for line in response.text.strip().split("\n")
        if line.startswith("data: ") and "[DONE]" not in line
    ]


@pytest.mark.asyncio
async def test_stream_emits_reasoning_events_separately(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """В1: события reasoning идут отдельным типом, не смешиваясь с токенами."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_stream_with_reasoning(monkeypatch)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert response.status_code == 200

    events = _parse_sse_events(response)
    reasoning_events = [e for e in events if e["type"] == "reasoning"]
    token_events = [e for e in events if e["type"] == "token"]

    assert "".join(e["v"] for e in reasoning_events) == REASONING_TEXT
    assert "".join(e["v"] for e in token_events) == ANSWER_TEXT
    # Рассуждения предшествуют ответу
    assert events[0]["type"] == "reasoning"
    assert events[-1]["type"] == "token"
    assert response.text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_stream_reasoning_saved_to_assistant_meta(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Трейс сохраняется в meta ассистента; контент — только ответ."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_stream_with_reasoning(monkeypatch)

    await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )

    messages = await _get_saved_messages(api_client)
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["content"] == ANSWER_TEXT
    assert assistant["meta"]["reasoning_content"] == REASONING_TEXT


@pytest.mark.asyncio
async def test_stream_without_reasoning_has_no_reasoning_events(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Модель без рассуждений — событий типа reasoning нет, в meta пусто."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        yield {"type": "token", "v": "plain answer"}

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    events = _parse_sse_events(response)
    assert all(e["type"] == "token" for e in events)

    messages = await _get_saved_messages(api_client)
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert "reasoning_content" not in assistant["meta"]


@pytest.mark.asyncio
async def test_non_streaming_returns_reasoning_content(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """В1: в non-streaming ответе — отдельное поле reasoning_content."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_complete_with_reasoning(monkeypatch, REASONING_TEXT)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "complete"
    assert body["content"] == ANSWER_TEXT
    assert body["reasoning_content"] == REASONING_TEXT

    messages = await _get_saved_messages(api_client)
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["meta"]["reasoning_content"] == REASONING_TEXT


@pytest.mark.asyncio
async def test_non_streaming_without_reasoning_field_is_null(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без рассуждений поле отсутствует в содержимом (null в JSON)."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_complete_with_reasoning(monkeypatch, reasoning=None)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == ANSWER_TEXT
    assert body["reasoning_content"] is None
