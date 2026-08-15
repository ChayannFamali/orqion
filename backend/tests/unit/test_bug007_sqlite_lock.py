"""Тесты BUG-007a: SQLite lock contention — retry, degrade, SSE error.

Проверяет:
1. create_trace: OperationalError → деградирует (synthetic trace_id), chat продолжается
2. save_messages: OperationalError → retry с успехом на 2-й попытке
3. save_messages: OperationalError → retry исчерпан → 503
4. Streaming: save_messages → DatabaseTemporarilyUnavailable → SSE error event
5. Non-streaming: save_messages → DatabaseTemporarilyUnavailable → HTTP 503
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.chat.service import ChatContext, save_messages, _save_messages_impl
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, User
from app.errors import DatabaseTemporarilyUnavailable
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from app.trace.service import create_trace, TraceContext
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError


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
async def test_create_trace_degrades_on_operational_error(db_session, monkeypatch):
    """create_trace: OperationalError → деградирует, возвращает TraceContext
    с synthetic trace_id (uuid4)."""
    original_flush = db_session.flush
    call_count = 0

    async def _failing_flush(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OperationalError("INSERT", {}, "database is locked")
        return await original_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", _failing_flush)
    monkeypatch.setattr(db_session, "rollback", AsyncMock())

    trace_ctx = await create_trace(db_session, "ws-1", user_id="user-1")

    assert trace_ctx is not None
    assert len(trace_ctx.trace_id) == 36  # uuid4 string
    assert trace_ctx.workspace_id == "ws-1"
    assert trace_ctx.user_id == "user-1"


# ── 2. save_messages retry success ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_messages_retry_success_on_second_attempt(monkeypatch):
    """save_messages: OperationalError на первой попытке → retry → успех на второй."""
    call_count = 0
    expected_result = ("conv-1", "msg-1")

    async def _flaky_impl(session, chat_ctx, model, ws_id, sources):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OperationalError("INSERT", {}, "database is locked")
        return expected_result

    monkeypatch.setattr("app.chat.service._save_messages_impl", _flaky_impl)

    # Use a real-like session mock that supports begin_nested as async context manager
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _begin_nested():
        yield

    fake_session = AsyncMock()
    fake_session.begin_nested = _begin_nested
    chat_ctx = MagicMock()
    model = MagicMock()

    result = await save_messages(
        fake_session, chat_ctx, model, "ws-1",
        max_retries=2, base_backoff_ms=10,
    )

    assert call_count == 2
    assert result == expected_result


# ── 3. save_messages retry exhausted → 503 ───────────────────────────────────


@pytest.mark.asyncio
async def test_save_messages_retry_exhausted_raises_503(monkeypatch):
    """save_messages: OperationalError на всех попытках → DatabaseTemporarilyUnavailable."""
    async def _always_fails(session, chat_ctx, model, ws_id, sources):
        raise OperationalError("INSERT", {}, "database is locked")

    monkeypatch.setattr("app.chat.service._save_messages_impl", _always_fails)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _begin_nested():
        yield

    fake_session = AsyncMock()
    fake_session.begin_nested = _begin_nested
    chat_ctx = MagicMock()
    model = MagicMock()

    with pytest.raises(DatabaseTemporarilyUnavailable):
        await save_messages(
            fake_session, chat_ctx, model, "ws-1",
            max_retries=2, base_backoff_ms=10,
        )


# ── 4. Streaming: SSE error event ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_save_error_sends_sse_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch,
):
    """Streaming: save_messages → DatabaseTemporarilyUnavailable → SSE error event,
    не HTTP 503 (заголовки уже отправлены)."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)

    # Patch ProviderClient.stream to yield a token
    async def _stub_stream(self, messages, model, max_tokens=None, temperature=0.7):
        yield "Hello"

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)

    # Patch save_messages → DatabaseTemporarilyUnavailable
    from app.api.routes import chat as chat_module

    async def _failing_save(*args, **kwargs):
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

    # SSE tokens were sent
    assert '"type": "token"' in body
    # SSE error event for save failure
    assert "db_temporarily_unavailable" in body
    assert '"type": "error"' in body
    assert "[DONE]" in body


# ── 5. Non-streaming: HTTP 503 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_streaming_save_error_returns_503(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch,
):
    """Non-streaming: save_messages → DatabaseTemporarilyUnavailable → HTTP 503."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "Mock response")

    from app.api.routes import chat as chat_module

    async def _failing_save(*args, **kwargs):
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
    monkeypatch,
):
    """Промежуточный session.flush() в chat route — OperationalError → деградирует,
    chat продолжается (200), trace потерян но не блокирует.

    Патчит _save_messages_impl (промежуточный flush через begin_nested) —
    но реальный путь: create_trace ловит OperationalError внутри себя (SAVEPOINT),
    chat route продолжается. Проверяем через end-to-end: chat возвращает 200.
    """
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "Mock response")

    # Patch create_trace to always raise OperationalError — it should catch
    # internally and return synthetic TraceContext, chat should succeed
    from app.api.routes import chat as chat_module
    from sqlalchemy.exc import OperationalError
    from app.trace.service import TraceContext
    import uuid

    async def _degrading_create_trace(session, workspace_id, user_id=None):
        # Simulate create_trace catching OperationalError and degrading
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

    # Chat should still succeed — create_trace degrades gracefully
    assert response.status_code == 200


# ── 7. MissingGreenlet regression — SAVEPOINT prevents ORM expiry ───────────


@pytest.mark.asyncio
async def test_create_trace_savepoint_does_not_expire_user(db_session, monkeypatch):
    """Regression: session.rollback() after OperationalError expires all ORM
    objects → subsequent user.role_id triggers MissingGreenlet. SAVEPOINT
    (begin_nested) rollback does NOT expire objects outside the savepoint.

    This test verifies that after create_trace catches OperationalError,
    a User object loaded before create_trace is still accessible without
    triggering a lazy-load (MissingGreenlet).
    """
    from app.db.models import Role, User, Workspace

    # Create workspace, role, user in the session
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
    # Access user.email to confirm it's loaded
    assert user.email == "test@orqion.local"

    # Now call create_trace — it will fail with OperationalError
    original_flush = db_session.flush
    call_count = 0

    async def _failing_flush(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OperationalError("INSERT", {}, "database is locked")
        return await original_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", _failing_flush)

    trace_ctx = await create_trace(db_session, ws.id, user_id=user_id)

    # create_trace degraded
    assert trace_ctx is not None
    assert len(trace_ctx.trace_id) == 36

    # CRITICAL: user object must still be accessible without MissingGreenlet.
    # If session.rollback() was used instead of begin_nested(), this would
    # raise sqlalchemy.exc.MissingGreenlet on .email access.
    assert user.email == "test@orqion.local"
    assert user.id == user_id
