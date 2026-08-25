"""Т-445 (каркас): решение по режиму рассуждения записывается в мету.

Варианты дизайн-ревью: Б1 — ручной флаг ``reasoning_toggleable``; А3 —
матрица эффективного режима (off/on фиксирует политика, при ``optional``
учитывается выбор на уровне сообщения, по умолчанию ``auto``); честная
запись ``reasoning_note``, если режим запрошен, но модель не поддерживает
переключение. Конкретный параметр запроса провайдеру пока НЕ отправляется —
в каркасе решение только записывается (вариант I).

Провайдер подменяется заглушкой — обращения к сети запрещены (AGENTS.md §12.2).
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
from app.providers.client import ProviderClient
from fastapi import FastAPI

ANSWER_TEXT = "plain answer"


async def _login_with_reasoning(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    reasoning: str,
    email: str,
) -> str:
    """Создаёт роль с заданным ``policy.reasoning`` и логинит."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        policy = BUILTIN_ROLES["developer"].model_dump()
        policy["reasoning"] = reasoning
        role = Role(
            workspace_id=workspace_id,
            name=f"role-{reasoning}-{email}",
            is_builtin=False,
            policy=policy,
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email=email,
            password_hash=hash_password("password-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_model(app_fixture: FastAPI, *, toggleable: bool) -> str:
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
            alias=f"local/model-{int(toggleable)}",
            upstream_name="upstream",
            locality="local",
            enabled=True,
            reasoning_toggleable=toggleable,
        )
        session.add(model)
        await session.commit()
        return model.id


def _patch_plain_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": ANSWER_TEXT}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)


async def _assistant_meta(api_client: httpx.AsyncClient) -> dict[str, Any]:
    convs = await api_client.get("/api/conversations")
    conv_id = convs.json()["conversations"][0]["id"]
    conv = await api_client.get(f"/api/conversations/{conv_id}")
    assistant = next(m for m in conv.json()["messages"] if m["role"] == "assistant")
    meta: dict[str, Any] = assistant["meta"]
    return meta


async def _chat(api_client: httpx.AsyncClient, reasoning_mode: str | None = None) -> None:
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }
    if reasoning_mode is not None:
        payload["reasoning_mode"] = reasoning_mode
    response = await api_client.post("/api/chat", json=payload)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_policy_off_fixes_mode_off(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Политика ``off`` фиксирует режим; не-переключаемая модель даёт честную запись."""
    await _login_with_reasoning(api_client, app_fixture, "off", "off@orqion.local")
    await _seed_model(app_fixture, toggleable=False)
    _patch_plain_complete(monkeypatch)

    await _chat(api_client)

    meta = await _assistant_meta(api_client)
    assert meta["reasoning_mode"] == "off"
    assert "не поддерживает переключение" in meta["reasoning_note"]


@pytest.mark.asyncio
async def test_policy_off_toggleable_model_no_note(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Режим ``off`` у переключаемой модели — без честной записи (переключение доступно)."""
    await _login_with_reasoning(api_client, app_fixture, "off", "off2@orqion.local")
    await _seed_model(app_fixture, toggleable=True)
    _patch_plain_complete(monkeypatch)

    await _chat(api_client)

    meta = await _assistant_meta(api_client)
    assert meta["reasoning_mode"] == "off"
    assert "reasoning_note" not in meta


@pytest.mark.asyncio
async def test_optional_default_is_auto(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Политика ``optional`` без выбора на сообщении — режим ``auto``, без записи."""
    await _login_with_reasoning(api_client, app_fixture, "optional", "opt@orqion.local")
    await _seed_model(app_fixture, toggleable=True)
    _patch_plain_complete(monkeypatch)

    await _chat(api_client)

    meta = await _assistant_meta(api_client)
    assert meta["reasoning_mode"] == "auto"
    assert "reasoning_note" not in meta


@pytest.mark.asyncio
async def test_optional_per_message_choice_respected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Политика ``optional`` + выбор на сообщении — учитывается выбор (Г1)."""
    await _login_with_reasoning(api_client, app_fixture, "optional", "opt2@orqion.local")
    await _seed_model(app_fixture, toggleable=True)
    _patch_plain_complete(monkeypatch)

    await _chat(api_client, reasoning_mode="on")

    meta = await _assistant_meta(api_client)
    assert meta["reasoning_mode"] == "on"
    assert "reasoning_note" not in meta


@pytest.mark.asyncio
async def test_per_message_choice_ignored_when_policy_fixed(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Политика фиксирует режим — выбор на сообщении игнорируется."""
    await _login_with_reasoning(api_client, app_fixture, "on", "on@orqion.local")
    await _seed_model(app_fixture, toggleable=True)
    _patch_plain_complete(monkeypatch)

    # Пытаемся выключить, но политика фиксирует "on"
    await _chat(api_client, reasoning_mode="off")

    meta = await _assistant_meta(api_client)
    assert meta["reasoning_mode"] == "on"
    assert "reasoning_note" not in meta
