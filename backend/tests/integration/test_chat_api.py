"""Тесты чата: стриминг, обрыв, ошибка провайдера, enforce, сохранение, auto-title.

Провайдер подменяется заглушкой — обращения к сети запрещены (AGENTS.md §12.2).
"""

from __future__ import annotations

import asyncio
import json
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
    enabled: bool = True,
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
            enabled=True,
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
            max_input_tokens=32000,
            enabled=enabled,
        )
        session.add(model)
        await session.commit()
        return model.id


def _patch_provider_client(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    """Подменяет ProviderClient.stream и complete — возврат заглушки."""

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


@pytest.mark.asyncio
async def test_stream_chat_returns_tokens(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Стриминг возвращает SSE-события token и [DONE]."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "Hello world from model")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    lines = response.text.strip().split("\n")
    token_events = [
        json.loads(l[6:]) for l in lines if l.startswith("data: ") and "[DONE]" not in l
    ]
    assert len(token_events) > 0
    assert all(e["type"] == "token" for e in token_events)
    assert response.text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_non_stream_chat_returns_complete(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Не-стриминговый режим возвращает JSON с content."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "Hello from model")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "complete"
    assert body["content"] == "Hello from model"
    assert "usage" in body


@pytest.mark.asyncio
async def test_message_saved_after_stream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """После стриминга сообщение сохраняется в БД."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "Hello world")

    await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": True,
        },
    )

    # Проверяем сохранение
    convs = await api_client.get("/api/conversations")
    assert convs.json()["total"] == 1
    conv_id = convs.json()["conversations"][0]["id"]

    conv = await api_client.get(f"/api/conversations/{conv_id}")
    messages = conv.json()["messages"]
    assert len(messages) == 2  # user + assistant
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Say hello"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"].strip() == "Hello world"


@pytest.mark.asyncio
async def test_auto_title_from_first_message(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Заголовок диалога — первые 80 символов первого user-сообщения."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "ok")

    resp = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "How do I configure FastAPI?"}],
            "stream": False,
        },
    )
    assert resp.status_code == 200, f"Chat POST failed: {resp.status_code} {resp.text}"

    convs = await api_client.get("/api/conversations")
    assert convs.json()["total"] == 1, f"Expected 1 conversation, got {convs.json()}"
    title = convs.json()["conversations"][0]["title"]
    assert title == "How do I configure FastAPI?"


@pytest.mark.asyncio
async def test_auto_title_truncated_at_80(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Длинный заголовок обрезается до 80 символов."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "ok")

    long_msg = "A" * 200
    await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": long_msg}],
            "stream": False,
        },
    )

    convs = await api_client.get("/api/conversations")
    title = convs.json()["conversations"][0]["title"]
    assert len(title) == 80


@pytest.mark.asyncio
async def test_provider_error_emitted_as_event(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ошибка провайдера — событие error, не разрыв соединения (S-13)."""

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

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    lines = response.text.strip().split("\n")
    error_events = [
        json.loads(l[6:])
        for l in lines
        if l.startswith("data: ") and "[DONE]" not in l and "error" in l[6:]
    ]
    assert len(error_events) == 1
    assert error_events[0]["type"] == "error"
    # [DONE] всё равно присутствует
    assert response.text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_chat_with_existing_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запрос с conversation_id добавляет сообщения в существующий диалог."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "second reply")

    # Первый запрос — создаёт диалог
    await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "first message"}],
            "stream": False,
        },
    )
    convs = await api_client.get("/api/conversations")
    conv_id = convs.json()["conversations"][0]["id"]
    assert convs.json()["conversations"][0]["message_count"] == 2

    # Второй запрос в тот же диалог
    await api_client.post(
        "/api/chat",
        json={
            "conversation_id": conv_id,
            "messages": [{"role": "user", "content": "second message"}],
            "stream": False,
        },
    )

    conv = await api_client.get(f"/api/conversations/{conv_id}")
    messages = conv.json()["messages"]
    assert len(messages) == 4  # 2 user + 2 assistant


@pytest.mark.asyncio
async def test_chat_nonexistent_conversation_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запрос с несуществующим conversation_id → 404."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "ok")

    response = await api_client.post(
        "/api/chat",
        json={
            "conversation_id": "nonexistent-id",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_enforce_blocks_external_for_k3(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """К3 + внешняя модель → отказ DataClassViolation (ADR-12, через enforce)."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture, model_alias="external/gpt-4", locality="external")
    _patch_provider_client(monkeypatch, "ok")

    # Удаляем все routing rules, чтобы правило К2/К3 не срабатывало в router.
    # Фильтр в enforce всё равно блокирует.
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        from app.db.models import RoutingRule
        from sqlalchemy import delete

        await session.execute(delete(RoutingRule).where(RoutingRule.workspace_id == workspace_id))
        await session.commit()

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "secret data"}],
            "corpus_data_class": "К3",
            "stream": False,
        },
    )
    # NoRouteAvailable (503) — нет локальных моделей после К3-фильтра
    assert response.status_code in (403, 503)


@pytest.mark.asyncio
async def test_unauthenticated_chat_rejected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Без логина — 401."""
    response = await api_client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_model_id_saved_on_assistant_message(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """model_id сохраняется на сообщении ассистента."""
    await _login_as_admin(api_client, app_fixture)
    model_id = await _seed_provider_and_model(app_fixture)
    _patch_provider_client(monkeypatch, "reply")

    await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )

    convs = await api_client.get("/api/conversations")
    conv_id = convs.json()["conversations"][0]["id"]
    conv = await api_client.get(f"/api/conversations/{conv_id}")
    messages = conv.json()["messages"]

    assistant_msg = next(m for m in messages if m["role"] == "assistant")
    assert assistant_msg["model_id"] == model_id


@pytest.mark.asyncio
async def test_policy_models_filters_routing_candidates(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """support (models=["local/*"]) + авто-роутинг → выбрана локальная модель.

    Без фильтра policy.models до маршрутизации support-роль могла бы получить
    внешнюю модель через авто-роут. Фильтр сужает кандидаты ДО select_model.
    """
    support_policy = BUILTIN_ROLES["support"].model_dump()
    await _login_as_admin(api_client, app_fixture, role_name="support", policy=support_policy)

    # Создаём local и external модель
    local_model_id = await _seed_provider_and_model(
        app_fixture, model_alias="local/qwen3-8b", upstream_name="qwen3-8b", locality="local"
    )
    await _seed_provider_and_model(
        app_fixture, model_alias="external/gpt-4", upstream_name="gpt-4", locality="external"
    )
    _patch_provider_client(monkeypatch, "local reply")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["content"] == "local reply"

    # Проверяем, что выбрана локальная модель
    convs = await api_client.get("/api/conversations")
    conv_id = convs.json()["conversations"][0]["id"]
    conv = await api_client.get(f"/api/conversations/{conv_id}")
    messages = conv.json()["messages"]
    assistant_msg = next(m for m in messages if m["role"] == "assistant")
    assert assistant_msg["model_id"] == local_model_id


@pytest.mark.asyncio
async def test_stream_abort_closes_upstream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При обрыве клиентского соединения upstream-генератор закрывается.

    T-305: is_disconnected() + gen.aclose() гарантируют, что генерация
    у провайдера останавливается, а не продолжается вхолостую.

    Тест на unit-уровне: вызывает _stream_with_save напрямую с mock Request,
    у которого is_disconnected() возвращает True после второго чанка.
    httpx ASGI transport не эмулирует реальный disconnect, поэтому
    интеграционный тест через api_client не подходит.
    """
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)

    upstream_state: dict[str, bool] = {"closed": False, "completed": False}

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        try:
            for i in range(100):
                yield f"token{i} "
                await asyncio.sleep(0.01)
            upstream_state["completed"] = True
        except GeneratorExit:
            upstream_state["closed"] = True
            raise

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)

    # Mock Request: is_disconnected возвращает True после 2-й проверки
    disconnect_counter: dict[str, int] = {"n": 0}

    class MockRequest:
        async def is_disconnected(self) -> bool:
            disconnect_counter["n"] += 1
            return disconnect_counter["n"] > 2

    # Мокаем span и save_messages, чтобы не тянуть полную БД-инфраструктуру
    class _NoopSpan:
        async def __aenter__(self) -> None:
            pass

        async def __aexit__(self, *args: object) -> None:
            pass

    def _noop_span(ctx: Any, name: str) -> _NoopSpan:
        return _NoopSpan()

    from app.api.routes import chat as chat_module
    from app.chat import service as chat_service

    async def _noop_save(session: Any, ctx: Any, model: Any, ws_id: str) -> tuple[str, str]:
        return ("conv-1", "msg-1")

    async def _noop_usage(session: Any, ws_id: str, record: Any) -> None:
        pass

    async def _noop_finalize(session: Any, ctx: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(chat_module, "span", _noop_span)
    monkeypatch.setattr(chat_module, "save_messages", _noop_save)
    monkeypatch.setattr(chat_service, "save_messages", _noop_save)
    monkeypatch.setattr(chat_module, "record_usage", _noop_usage)
    monkeypatch.setattr(chat_module, "finalize_trace", _noop_finalize)
    monkeypatch.setattr(
        chat_module,
        "_build_usage_record",
        lambda *args: None,
    )

    # Минимальный chat_ctx mock
    from app.chat.service import ChatContext
    from app.policy.models import Policy

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id

    async with factory() as session:
        from app.db.models import Model as DBModel
        from app.db.models import Provider as DBProvider

        model_row = (
            await session.execute(select(DBModel).where(DBModel.workspace_id == workspace_id))
        ).scalar_one()
        provider_row = (
            await session.execute(select(DBProvider).where(DBProvider.workspace_id == workspace_id))
        ).scalar_one()

    chat_ctx = ChatContext(
        user=None,  # type: ignore[arg-type]
        policy=Policy(capabilities=["chat"]),
        messages=[{"role": "user", "content": "hi"}],
        model_alias=None,
        max_tokens=100,
        temperature=0.7,
        stream=True,
        corpus_data_class=None,
        corpus_name=None,
        task_type=None,
        conversation_id=None,
    )

    from app.trace.service import TraceContext

    trace_ctx = TraceContext(trace_id="test-trace", workspace_id=workspace_id)

    # Вызываем _stream_with_save напрямую
    chunks: list[str] = []
    stream_gen = chat_module._stream_with_save(
        MockRequest(),  # type: ignore[arg-type]
        chat_ctx,
        model_row,
        provider_row,
        [],
        app_fixture.state.secret_key,
        workspace_id,
        factory,
        trace_ctx,
    )
    async for chunk in stream_gen:
        chunks.append(chunk)
        if len(chunks) > 1:
            break

    # Явно закрываем генератор _stream_with_save — триггерит finally
    await stream_gen.aclose()

    # Даём event loop время на обработку gen.aclose() → upstream_gen.aclose()
    await asyncio.sleep(0.3)

    assert upstream_state["closed"] is True, "upstream generator was not closed on disconnect"
    assert upstream_state["completed"] is False, "upstream completed despite client disconnect"


@pytest.mark.asyncio
async def test_selected_model_alias_becomes_primary(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-012: выбранная в запросе модель становится primary, не candidates[0].

    Регрессия: select_model игнорировал model_alias в итоге — ответ приходил
    от первой модели множества, а выбор пользователя был только условием правил.
    """
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture, "local/first", "first-upstream")
    await _seed_provider_and_model(app_fixture, "local/second", "second-upstream")

    call_log: list[str] = []

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        call_log.append(model)
        return {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "model_alias": "local/second",
            "stream": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "local/second"
    assert call_log == ["second-upstream"]

    # T-307: routing-спан явно говорит «user selection», не «default»
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        traces = (await session.execute(select(Trace))).scalars().all()
        assert traces, "trace не записан"
        spans = (
            (await session.execute(select(Span).where(Span.trace_id == traces[-1].id)))
            .scalars()
            .all()
        )
        routing_spans = [s for s in spans if s.name == "routing"]
        assert len(routing_spans) == 1
        assert routing_spans[0].payload["model"] == "local/second"
        assert routing_spans[0].payload["reason"] == "user selection (local/second)"
