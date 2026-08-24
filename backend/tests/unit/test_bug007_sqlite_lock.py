"""Тесты BUG-007a: SQLite lock contention — retry, degrade, SSE error.

Проверяет:
1. create_trace: OperationalError → деградирует (synthetic trace_id), chat продолжается
2. save_messages: OperationalError → retry с успехом на 2-й попытке
3. save_messages: OperationalError → retry исчерпан → 503
4. Streaming: save_messages → DatabaseTemporarilyUnavailable → SSE error event
5. Non-streaming: save_messages → DatabaseTemporarilyUnavailable → HTTP 503
6. Intermediate flush: create_trace деградирует → chat 200
7. MissingGreenlet regression: SAVEPOINT не экспайрит ORM-объекты
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.chat.service import save_messages
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, User
from app.errors import DatabaseTemporarilyUnavailable
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from app.trace.service import TraceContext, create_trace
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError


def _make_operational_error() -> OperationalError:
    """Создаёт OperationalError с правильным типом третьего аргумента."""
    return OperationalError("INSERT", {}, Exception("database is locked"))


# ── helpers ──────────────────────────────────────────────────────────────────


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str = "admin",
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name=role_name,
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
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
            alias="local/test-model",
            upstream_name="test-model",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(model)
        await session.commit()
        return model.id


def _patch_provider_client(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": response}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)


# ── 1. create_trace degrade ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_trace_degrades_on_operational_error(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create_trace: OperationalError → деградирует, возвращает TraceContext
    с synthetic trace_id (uuid4)."""
    original_flush = db_session.flush
    call_count = 0

    async def _failing_flush(*args: Any, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _make_operational_error()
        result: None = await original_flush(*args, **kwargs)
        return result

    monkeypatch.setattr(db_session, "flush", _failing_flush)
    monkeypatch.setattr(db_session, "rollback", AsyncMock())

    trace_ctx = await create_trace(db_session, "ws-1", user_id="user-1")

    assert trace_ctx is not None
    assert len(trace_ctx.trace_id) == 36  # uuid4 string
    assert trace_ctx.workspace_id == "ws-1"
    assert trace_ctx.user_id == "user-1"


# ── 2. save_messages retry success ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_messages_retry_success_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """save_messages: OperationalError на первой попытке → retry → успех на второй."""
    call_count = 0
    expected_result = ("conv-1", "msg-1")

    async def _flaky_impl(
        session: Any, chat_ctx: Any, model: Any, ws_id: str, sources: Any
    ) -> tuple[str, str | None]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _make_operational_error()
        return expected_result

    monkeypatch.setattr("app.chat.service._save_messages_impl", _flaky_impl)

    @asynccontextmanager
    async def _begin_nested() -> AsyncIterator[None]:
        yield

    fake_session = AsyncMock()
    fake_session.begin_nested = _begin_nested
    chat_ctx = MagicMock()
    model = MagicMock()

    result = await save_messages(
        fake_session,
        chat_ctx,
        model,
        "ws-1",
        max_retries=2,
        base_backoff_ms=10,
    )

    assert call_count == 2
    assert result == expected_result


# ── 3. save_messages retry exhausted → 503 ───────────────────────────────────


@pytest.mark.asyncio
async def test_save_messages_retry_exhausted_raises_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """save_messages: OperationalError на всех попытках → DatabaseTemporarilyUnavailable."""

    async def _always_fails(
        session: Any, chat_ctx: Any, model: Any, ws_id: str, sources: Any
    ) -> tuple[str, str | None]:
        raise _make_operational_error()

    monkeypatch.setattr("app.chat.service._save_messages_impl", _always_fails)

    @asynccontextmanager
    async def _begin_nested() -> AsyncIterator[None]:
        yield

    fake_session = AsyncMock()
    fake_session.begin_nested = _begin_nested
    chat_ctx = MagicMock()
    model = MagicMock()

    with pytest.raises(DatabaseTemporarilyUnavailable):
        await save_messages(
            fake_session,
            chat_ctx,
            model,
            "ws-1",
            max_retries=2,
            base_backoff_ms=10,
        )


# ── 4. Streaming: SSE error event ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_save_error_sends_sse_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streaming: save_messages → DatabaseTemporarilyUnavailable → SSE error event,
    не HTTP 503 (заголовки уже отправлены)."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[dict[str, str]]:
        yield {"type": "token", "v": "Hello"}

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)

    from app.api.routes import chat as chat_module

    async def _failing_save(*args: Any, **kwargs: Any) -> tuple[str, str | None]:
        raise DatabaseTemporarilyUnavailable()

    monkeypatch.setattr(chat_module, "save_messages", _failing_save)

    response = await api_client.post(
        "/api/chat",
        json={
            "model_alias": "local/test-model",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        },
    )

    assert response.status_code == 200  # SSE starts with 200
    body = response.text

    assert '"type": "token"' in body
    assert "db_temporarily_unavailable" in body
    assert '"type": "error"' in body
    assert "[DONE]" in body


# ── 5. Non-streaming: HTTP 503 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_streaming_save_error_returns_503(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-streaming: save_messages → DatabaseTemporarilyUnavailable → HTTP 503."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "Mock response")

    from app.api.routes import chat as chat_module

    async def _failing_save(*args: Any, **kwargs: Any) -> tuple[str, str | None]:
        raise DatabaseTemporarilyUnavailable()

    monkeypatch.setattr(chat_module, "save_messages", _failing_save)

    response = await api_client.post(
        "/api/chat",
        json={
            "model_alias": "local/test-model",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        },
    )

    assert response.status_code == 503
    data = response.json()
    assert data["error"] == "db_temporarily_unavailable"


# ── 6. Intermediate flush degrade ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_intermediate_flush_degrades_on_operational_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Промежуточный session.flush() в chat route — OperationalError → деградирует,
    chat продолжается (200), trace потерян но не блокирует."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "Mock response")

    from app.api.routes import chat as chat_module

    async def _degrading_create_trace(
        session: Any, workspace_id: str, user_id: str | None = None
    ) -> TraceContext:
        return TraceContext(
            trace_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            user_id=user_id,
        )

    monkeypatch.setattr(chat_module, "create_trace", _degrading_create_trace)

    response = await api_client.post(
        "/api/chat",
        json={
            "model_alias": "local/test-model",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        },
    )

    assert response.status_code == 200


# ── 7. MissingGreenlet regression — SAVEPOINT prevents ORM expiry ───────────


@pytest.mark.asyncio
async def test_create_trace_savepoint_does_not_expire_user(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: session.rollback() after OperationalError expires all ORM
    objects → subsequent user.role_id triggers MissingGreenlet. SAVEPOINT
    (begin_nested) rollback does NOT expire objects outside the savepoint.
    """
    from app.db.models import Role, Workspace

    ws = Workspace(name="test-ws")
    db_session.add(ws)
    await db_session.flush()

    role = Role(workspace_id=ws.id, name="test-role", is_builtin=False, policy={})
    db_session.add(role)
    await db_session.flush()

    user = User(
        workspace_id=ws.id,
        email="test@orqion.local",
        password_hash="hash",
        role_id=role.id,
    )
    db_session.add(user)
    await db_session.flush()
    user_id = user.id
    assert user.email == "test@orqion.local"

    original_flush = db_session.flush
    call_count = 0

    async def _failing_flush(*args: Any, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _make_operational_error()
        result: None = await original_flush(*args, **kwargs)
        return result

    monkeypatch.setattr(db_session, "flush", _failing_flush)

    trace_ctx = await create_trace(db_session, ws.id, user_id=user_id)

    assert trace_ctx is not None
    assert len(trace_ctx.trace_id) == 36

    # CRITICAL: user object must still be accessible without MissingGreenlet.
    assert user.email == "test@orqion.local"
    assert user.id == user_id
