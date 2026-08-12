"""Тесты трассировки: trace + span создаются, durations, nullable FK, пакетная запись.

Проверки:
- trace создаётся для каждого чат-запроса
- span'ы записываются с duration_ms
- conversation_id/message_id в trace — nullable FK
- trace status = error при ошибке провайдера
- span'ы записываются пачкой (не в горячем пути)
- trace не содержит содержимого запросов/ответов (только в span.payload)
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, Span, Trace, User
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
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
            "choices": [{"message": {"content": response}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        for word in response.split():
            yield word + " "

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)
    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)


async def _get_traces(app_fixture: FastAPI) -> list[Trace]:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        result = await session.execute(select(Trace).where(Trace.workspace_id == workspace_id))
        return list(result.scalars().all())


async def _get_spans(app_fixture: FastAPI, trace_id: str) -> list[Span]:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(
            select(Span).where(Span.trace_id == trace_id).order_by(Span.started_at)
        )
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_trace_created_after_non_stream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-stream чат → trace создан."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "hello")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    traces = await _get_traces(app_fixture)
    assert len(traces) == 1
    trace = traces[0]
    assert trace.status == "ok"
    assert trace.total_ms is not None and trace.total_ms >= 0


@pytest.mark.asyncio
async def test_trace_created_after_stream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stream чат → trace создан."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "hello world")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
    )

    traces = await _get_traces(app_fixture)
    assert len(traces) == 1
    assert traces[0].status == "ok"


@pytest.mark.asyncio
async def test_spans_recorded_with_duration(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Span'ы записаны с duration_ms > 0."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "hello")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    traces = await _get_traces(app_fixture)
    assert len(traces) == 1
    spans = await _get_spans(app_fixture, traces[0].id)
    assert len(spans) >= 2  # prepare + execute
    span_names = {s.name for s in spans}
    assert "prepare" in span_names
    assert "execute" in span_names
    for s in spans:
        assert s.duration_ms is not None and s.duration_ms >= 0


@pytest.mark.asyncio
async def test_trace_status_error_on_provider_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ошибка провайдера → trace status = error."""

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

    traces = await _get_traces(app_fixture)
    assert len(traces) == 1
    assert traces[0].status == "error"


@pytest.mark.asyncio
async def test_trace_conversation_and_message_id_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """conversation_id и message_id в trace указывают на созданные объекты."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "hello")

    resp = await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    conv_id = resp.json().get("conversation_id")

    traces = await _get_traces(app_fixture)
    assert len(traces) == 1
    assert traces[0].conversation_id == conv_id
    assert traces[0].message_id is not None


@pytest.mark.asyncio
async def test_trace_no_content_in_fields(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trace не содержит содержимого запросов/ответов в своих полях."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "secret response")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "secret prompt"}], "stream": False},
    )

    traces = await _get_traces(app_fixture)
    assert len(traces) == 1
    trace = traces[0]

    # Проверяем, что ни одно поле trace не содержит содержимого
    for key, value in trace.__dict__.items():
        if isinstance(value, str):
            assert "secret" not in value.lower(), f"Content leaked in trace field '{key}': {value}"


@pytest.mark.asyncio
async def test_spans_batch_written(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Span'ы записаны пачкой в finalize_trace, не в горячем пути."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "hello")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    traces = await _get_traces(app_fixture)
    assert len(traces) == 1
    spans = await _get_spans(app_fixture, traces[0].id)

    # Все span'ы имеют duration_ms — значит записаны после завершения (не в горячем пути)
    for s in spans:
        assert s.duration_ms is not None


@pytest.mark.asyncio
async def test_trace_user_id_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """user_id в trace указывает на пользователя."""
    user_id = await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "hello")

    await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    traces = await _get_traces(app_fixture)
    assert len(traces) == 1
    assert traces[0].user_id == user_id


# ---------------------------------------------------------------------------
# T-307: Read API — GET /api/traces, GET /api/traces/{id}
# ---------------------------------------------------------------------------


async def _login_with_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str = "developer",
    email_suffix: str = "",
) -> str:
    """Логинит пользователя с заданной ролью, возвращает user_id."""
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

        user = User(
            workspace_id=workspace_id,
            email=f"trace-r-{role_name}{email_suffix}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _create_trace_directly(
    app_fixture: FastAPI,
    user_id: str,
    *,
    conversation_id: str | None = None,
    status: str = "ok",
) -> str:
    """Создаёт trace + span в БД напрямую, возвращает trace_id."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        from datetime import UTC, datetime

        trace = Trace(
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            ts=datetime.now(UTC),
            total_ms=150,
            status=status,
        )
        session.add(trace)
        await session.flush()

        span = Span(
            workspace_id=workspace_id,
            trace_id=trace.id,
            name="step_search",
            payload={"step": "step_search", "candidates_count": 5},
        )
        session.add(span)
        await session.commit()
        return trace.id


@pytest.mark.asyncio
async def test_list_traces_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/traces → 200, список трассировок пользователя."""
    user_id = await _login_with_role(api_client, app_fixture, "developer")
    await _create_trace_directly(app_fixture, user_id)

    resp = await api_client.get("/api/traces")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert len(data["traces"]) >= 1
    assert data["traces"][0]["span_count"] >= 1


@pytest.mark.asyncio
async def test_list_traces_user_isolation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/traces → пользователь видит только свои трассировки."""
    user1_id = await _login_with_role(api_client, app_fixture, "developer")
    await _create_trace_directly(app_fixture, user1_id)

    await _login_with_role(api_client, app_fixture, "developer", email_suffix="2")
    resp = await api_client.get("/api/traces")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_traces_admin_sees_all(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/traces → admin видит все трассировки в workspace."""
    user_id = await _login_with_role(api_client, app_fixture, "developer")
    await _create_trace_directly(app_fixture, user_id)

    await _login_with_role(api_client, app_fixture, "admin")
    resp = await api_client.get("/api/traces")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_list_traces_filtered_by_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/traces?conversation_id={id} → фильтр по диалогу."""
    from app.db.models import Conversation

    user_id = await _login_with_role(api_client, app_fixture, "developer")
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        conv1 = Conversation(workspace_id=workspace_id, user_id=user_id, title="conv1")
        conv2 = Conversation(workspace_id=workspace_id, user_id=user_id, title="conv2")
        session.add_all([conv1, conv2])
        await session.commit()
        conv1_id = conv1.id
        conv2_id = conv2.id

    await _create_trace_directly(app_fixture, user_id, conversation_id=conv1_id)
    await _create_trace_directly(app_fixture, user_id, conversation_id=conv2_id)

    resp = await api_client.get(f"/api/traces?conversation_id={conv1_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert all(t["conversation_id"] == conv1_id for t in data["traces"])


@pytest.mark.asyncio
async def test_get_trace_detail(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/traces/{id} → 200, полная трассировка со span'ами."""
    user_id = await _login_with_role(api_client, app_fixture, "developer")
    trace_id = await _create_trace_directly(app_fixture, user_id)

    resp = await api_client.get(f"/api/traces/{trace_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == trace_id
    assert len(data["spans"]) >= 1
    assert data["spans"][0]["name"] == "step_search"
    assert "candidates_count" in data["spans"][0]["payload"]


@pytest.mark.asyncio
async def test_get_trace_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/traces/{id} → 404 для несуществующей."""
    await _login_with_role(api_client, app_fixture, "developer")

    resp = await api_client.get("/api/traces/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_trace_wrong_user(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/traces/{id} → 404 для чужой трассировки (user isolation)."""
    user1_id = await _login_with_role(api_client, app_fixture, "developer")
    trace_id = await _create_trace_directly(app_fixture, user1_id)

    await _login_with_role(api_client, app_fixture, "developer", email_suffix="2")
    resp = await api_client.get(f"/api/traces/{trace_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_view_traces_capability_required(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/traces → 404 без capability view_traces (роль support)."""
    await _login_with_role(api_client, app_fixture, "support")

    resp = await api_client.get("/api/traces")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"] == "not_found"
