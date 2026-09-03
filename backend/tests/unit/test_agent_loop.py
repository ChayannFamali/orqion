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
from app.agent.tools import ToolSpec
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
) -> AgentRunConfig:
    embedding_backend = AsyncMock()
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = 1.0
    embedding_backend.embed.return_value = [vector]
    store = SQLiteVectorStore(str(tmp_path / "vec.db"))
    stores.append(store)
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

    monkeypatch.setattr(
        "app.agent.loop.get_tool_spec",
        lambda name: ToolSpec(
            name=name,
            description="деструктивная заглушка",
            parameters={"type": "object", "properties": {}},
            destructive=True,
        ),
    )

    result = await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])

    assert result.pending_confirmation is not None
    assert result.pending_confirmation["tool"] == "delete_everything"
    assert result.pending_confirmation["call_id"] == "call-danger"
    assert result.content == ""
