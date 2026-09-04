"""Т-502: цикл «модель → инструменты → модель» (решения 2, 4, 5, 8, 9).

Позитивные тесты требуют установленный langgraph (экстра ``orqion[agent]``)
и пропускаются без него. Провайдер подменяется заглушкой — обращения к
сети запрещены.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.agent.loop import AgentRunConfig, run_agent_loop
from app.agent.tools import AGENT_TOOL_SPECS, ResolvedTools, ServerEndpoint, ToolSpec
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.models import (
    AuditLog,
    Model,
    Provider,
    Role,
    UsageEvent,
    User,
    Workspace,
)
from app.errors import AgentRunLimitExceeded
from app.policy.models import Policy
from app.providers.client import ProviderClient
from app.rag.vector_store import EMBEDDING_DIM, SQLiteVectorStore
from app.trace.service import TraceContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_FINAL_ANSWER = "Ответ по найденным фрагментам"


@pytest.fixture
async def vector_stores() -> AsyncIterator[list[SQLiteVectorStore]]:
    """Хранилища, созданные тестом, закрываются после него (иначе процесс виснет)."""
    stores: list[SQLiteVectorStore] = []
    yield stores
    for store in stores:
        await store.close()


def _tool_call_payload(call_id: str = "call-1") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "search_corpus",
                                "arguments": '{"query": "вопрос по документам"}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }


def _final_payload() -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": _FINAL_ANSWER}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


async def _seed(db_session: AsyncSession) -> tuple[str, str, Model, Provider]:
    ws = Workspace(name="agent-loop-test")
    db_session.add(ws)
    await db_session.flush()
    role = Role(
        workspace_id=ws.id,
        name="agent-loop-role",
        is_builtin=False,
        policy={"models": ["*"], "corpora": ["*"]},
    )
    db_session.add(role)
    await db_session.flush()
    user = User(
        workspace_id=ws.id,
        email="agent-loop@orqion.local",
        password_hash=hash_password("pass-123"),
        role_id=role.id,
    )
    db_session.add(user)
    await db_session.flush()
    provider = Provider(
        workspace_id=ws.id,
        kind="openai",
        base_url="http://stub:1234",
        api_key_enc=None,
        enabled=True,
        capabilities={},
    )
    db_session.add(provider)
    await db_session.flush()
    model = Model(
        workspace_id=ws.id,
        provider_id=provider.id,
        alias="local/agent-loop",
        upstream_name="upstream",
        locality="local",
        enabled=True,
        supports_tools=True,
    )
    db_session.add(model)
    await db_session.flush()
    return ws.id, user.id, model, provider


def _make_config(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    workspace_id: str,
    user: User,
    model: Model,
    provider: Provider,
    stores: list[SQLiteVectorStore],
    *,
    max_steps: int = 5,
    max_tokens_per_run: int = 100_000,
    tools_registry: ResolvedTools | None = None,
) -> AgentRunConfig:
    embedding_backend = AsyncMock()
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = 1.0
    embedding_backend.embed.return_value = [vector]
    store = SQLiteVectorStore(str(tmp_path / "vec.db"))
    stores.append(store)
    registry = tools_registry or ResolvedTools(specs=list(AGENT_TOOL_SPECS))
    return AgentRunConfig(
        session=db_session,
        settings=test_settings,
        secret_key="test-secret",
        workspace_id=workspace_id,
        user=user,
        policy=Policy(models=["*"], corpora=["*"]),
        model=model,
        provider=provider,
        vector_store=store,
        embedding_backend=embedding_backend,
        corpora=[],
        corpus_names=[],
        corpus_data_class=None,
        conversation_id=None,
        rate_limiter=None,
        trace_ctx=TraceContext(trace_id="trace-loop", workspace_id=workspace_id),
        max_steps=max_steps,
        max_tokens_per_run=max_tokens_per_run,
        tools_registry=registry,
    )


def _patch_model(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any]],
) -> dict[str, int]:
    """Подменяет complete_tools последовательностью ответов провайдера."""
    calls = {"n": 0}

    async def _stub(
        self: ProviderClient,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        idx = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[idx]

    monkeypatch.setattr(ProviderClient, "complete_tools", _stub)
    return calls


@pytest.mark.asyncio
async def test_cycle_model_tool_model(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Цикл: модель запрашивает поиск → инструмент → модель отвечает."""
    pytest.importorskip("langgraph")
    workspace_id, user_id, model, provider = await _seed(db_session)
    user = await db_session.get(User, user_id)
    assert user is not None
    cfg = _make_config(
        db_session,
        test_settings,
        tmp_path,
        workspace_id,
        user,
        model,
        provider,
        vector_stores,
    )
    calls = _patch_model(monkeypatch, [_tool_call_payload(), _final_payload()])

    result = await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])

    assert result.content == _FINAL_ANSWER
    assert calls["n"] == 2
    assert result.model_calls == 2
    # Шаги: модель (запрос инструмента), инструмент, модель (ответ).
    assert len(result.steps) == 3
    assert result.steps[0].kind == "model"
    assert result.steps[1].kind == "tool"
    assert result.steps[1].decision == "allow"
    assert result.steps[2].kind == "model"
    assert result.pending_confirmation is None
    # Биллинг: каждый вызов модели — отдельный usage_event (пункт 8).
    events = (await db_session.execute(select(UsageEvent))).scalars().all()
    assert len(events) == 2
    assert all(e.status == "ok" for e in events)
    assert result.tokens_in == 12
    assert result.tokens_out == 5
    # Аудит инструмента: компактный факт с решением.
    audits = (await db_session.execute(select(AuditLog))).scalars().all()
    tool_audits = [a for a in audits if a.action == "agent.tool.search_corpus"]
    assert len(tool_audits) == 1
    assert tool_audits[0].meta is not None
    assert tool_audits[0].meta["decision"] == "allow"

    # Трассировка: факты приёмки — схема ``tools`` ушла в запросе, первый
    # вызов вернул структурированный ``tool_calls``, второй — текст.
    payloads = {rec.name: rec.payload for rec in cfg.trace_ctx.spans}
    call1 = payloads["agent.model_call.1"]
    assert call1["tools"]  # параметр ``tools`` присутствует в составе запроса
    assert call1["response_has_tool_calls"] is True
    assert call1["tool_calls"] == [{"id": "call-1", "name": "search_corpus"}]
    call2 = payloads["agent.model_call.2"]
    assert call2["response_has_tool_calls"] is False


@pytest.mark.asyncio
async def test_step_limit_stops_run(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Пункт 4: лимит числа шагов останавливает прогон."""
    pytest.importorskip("langgraph")
    workspace_id, user_id, model, provider = await _seed(db_session)
    user = await db_session.get(User, user_id)
    assert user is not None
    cfg = _make_config(
        db_session,
        test_settings,
        tmp_path,
        workspace_id,
        user,
        model,
        provider,
        vector_stores,
        max_steps=1,
    )
    # Модель всегда запрашивает инструмент — цикл не завершается сам.
    _patch_model(monkeypatch, [_tool_call_payload()])

    with pytest.raises(AgentRunLimitExceeded) as exc_info:
        await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])
    constraint = exc_info.value.constraint or {}
    assert constraint["type"] == "steps"
    assert constraint["limit"] == 1


@pytest.mark.asyncio
async def test_token_budget_stops_run(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Пункт 4: суммарные токены прогона — предохранитель поверх биллинга."""
    pytest.importorskip("langgraph")
    workspace_id, user_id, model, provider = await _seed(db_session)
    user = await db_session.get(User, user_id)
    assert user is not None
    cfg = _make_config(
        db_session,
        test_settings,
        tmp_path,
        workspace_id,
        user,
        model,
        provider,
        vector_stores,
        max_tokens_per_run=10,
    )
    # Первый вызов: 5+2=7 токенов (проходит), второй: +7+3=17 > 10.
    calls = _patch_model(monkeypatch, [_tool_call_payload(), _final_payload()])

    with pytest.raises(AgentRunLimitExceeded) as exc_info:
        await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])
    constraint = exc_info.value.constraint or {}
    assert constraint["type"] == "tokens"
    assert constraint["limit"] == 10
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_destructive_tool_requests_confirmation(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Пункт 9: деструктивный инструмент останавливает прогон до выполнения."""
    pytest.importorskip("langgraph")
    workspace_id, user_id, model, provider = await _seed(db_session)
    user = await db_session.get(User, user_id)
    assert user is not None
    registry = ResolvedTools(
        specs=[
            ToolSpec(
                name="delete_everything",
                description="деструктивная заглушка",
                parameters={"type": "object", "properties": {}},
                destructive=True,
            )
        ]
    )
    cfg = _make_config(
        db_session,
        test_settings,
        tmp_path,
        workspace_id,
        user,
        model,
        provider,
        vector_stores,
        tools_registry=registry,
    )

    destructive_call = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-danger",
                            "type": "function",
                            "function": {
                                "name": "delete_everything",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
    }
    _patch_model(monkeypatch, [destructive_call])

    result = await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])

    assert result.pending_confirmation is not None
    assert result.pending_confirmation["tool"] == "delete_everything"
    assert result.pending_confirmation["call_id"] == "call-danger"
    # Прогон остановлен до выполнения: пользователю показан запрос
    # подтверждения (шаг и текст), действие не выполнено.
    assert result.content
    assert "подтверждение" in result.content.lower()
    confirmation_steps = [s for s in result.steps if s.kind == "confirmation"]
    assert len(confirmation_steps) == 1
    assert confirmation_steps[0].decision == "pending"
    assert confirmation_steps[0].name == "delete_everything"


@pytest.mark.asyncio
async def test_destructive_tool_approved_executes(
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Пункт 9: одобренный деструктивный инструмент исполняется.

    Разрешение действует на конкретный вызов: имя и аргументы должны
    совпасть с показанными пользователю; факт одобрения — дуальный
    аудит (шаг прогона, запись в журнале, спан трассировки).
    """
    pytest.importorskip("langgraph")
    workspace_id, user_id, model, provider = await _seed(db_session)
    user = await db_session.get(User, user_id)
    assert user is not None
    registry = ResolvedTools(
        specs=[
            ToolSpec(
                name="wiki.purge_cache",
                description="деструктивная заглушка",
                parameters={"type": "object", "properties": {"item": {"type": "string"}}},
                destructive=True,
                source="mcp:wiki",
                server_name="wiki",
                mcp_tool_name="purge_cache",
            )
        ],
        servers={"wiki": ServerEndpoint(url="http://127.0.0.1:1/mcp", api_key_enc=None)},
    )
    cfg = _make_config(
        db_session,
        test_settings,
        tmp_path,
        workspace_id,
        user,
        model,
        provider,
        vector_stores,
        tools_registry=registry,
    )

    # По подтверждению вызов исполняется напрямую, а модель вызывается
    # один раз — на финальный ответ по результату исполнения.
    final_call = {
        "choices": [{"message": {"content": _FINAL_ANSWER}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    _patch_model(monkeypatch, [final_call])

    from app.mcp.client import ToolCallResult

    executed: list[tuple[str, dict[str, object]]] = []

    async def fake_call_tool(
        conn: object, tool_name: str, arguments: dict[str, object], timeout: float
    ) -> ToolCallResult:
        executed.append((tool_name, arguments))
        return ToolCallResult(text="кэш очищен", is_error=False)

    monkeypatch.setattr("app.mcp.client.call_tool", fake_call_tool)

    result = await run_agent_loop(
        cfg,
        [{"role": "user", "content": "вопрос"}],
        approved_tool_call={
            "tool": "wiki.purge_cache",
            "args": {"item": "x"},
            "call_id": "call-danger",
        },
    )

    # Запрос подтверждения не возвращается — действие исполнено.
    assert result.pending_confirmation is None
    assert executed == [("purge_cache", {"item": "x"})]
    assert result.content == _FINAL_ANSWER
    approve_steps = [
        s for s in result.steps if s.kind == "confirmation" and s.decision == "approve"
    ]
    assert len(approve_steps) == 1

    # Дуальный аудит: одобрение и сам вызов — в журнале.
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    confirmations = [r for r in rows if r.action == "agent.tool.confirmation"]
    assert len(confirmations) == 1
    assert confirmations[0].meta is not None
    assert confirmations[0].meta["decision"] == "approve"
    assert confirmations[0].meta["tool"] == "wiki.purge_cache"
    tool_calls = [r for r in rows if r.action == "agent.tool.mcp"]
    assert len(tool_calls) == 1
    assert tool_calls[0].meta is not None
    assert tool_calls[0].meta["decision"] == "allow"
