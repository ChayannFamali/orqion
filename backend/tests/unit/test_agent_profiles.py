"""Т-509: профиль агента и остановка прогона — юнит-уровень.

Проверяется то, что интеграционные тесты через HTTP видят только по
побочным признакам:

- решение 7: флаг ``conversation.stop_requested`` читается МЕЖДУ шагами
  цикла. Текущий шаг не обрывается — ни вызов модели, ни партия
  инструментов; прогон завершается с ``stopped=True``, уже выполненные
  шаги и расход сохранены, флаг потреблён;
- решение 1/2: ``resolve_profile_for_run`` — fail-closed по образцу
  скиллов: призрак и отключённый профиль дают ``AgentProfileNotAvailable``
  (400), а не тихий откат к ad-hoc прогону.

Провайдер подменяется заглушкой — обращения к сети запрещены.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.agent.loop import STOPPED_CONTENT, AgentRunConfig, run_agent_loop
from app.agent.profiles import (
    consume_stop_request,
    is_stop_requested,
    resolve_profile_for_run,
)
from app.agent.tools import AGENT_TOOL_SPECS, ResolvedTools
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.models import (
    AgentProfile,
    AuditLog,
    Conversation,
    Model,
    Provider,
    Role,
    UsageEvent,
    User,
    Workspace,
)
from app.errors import AgentProfileNotAvailable
from app.policy.models import Policy
from app.providers.client import ProviderClient
from app.rag.vector_store import EMBEDDING_DIM, SQLiteVectorStore
from app.trace.service import TraceContext
from sqlalchemy import select, update
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
    ws = Workspace(name="agent-profiles-unit")
    db_session.add(ws)
    await db_session.flush()
    role = Role(
        workspace_id=ws.id,
        name="agent-profiles-role",
        is_builtin=False,
        policy={"models": ["*"], "corpora": ["*"]},
    )
    db_session.add(role)
    await db_session.flush()
    user = User(
        workspace_id=ws.id,
        email="agent-profiles@orqion.local",
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
        alias="local/profile-model",
        upstream_name="upstream",
        locality="local",
        enabled=True,
        supports_tools=True,
    )
    db_session.add(model)
    await db_session.flush()
    return ws.id, user.id, model, provider


async def _seed_conversation(
    db_session: AsyncSession,
    workspace_id: str,
    user_id: str,
    *,
    stop_requested: bool = False,
    agent_profile_id: str | None = None,
) -> str:
    conversation = Conversation(
        workspace_id=workspace_id,
        user_id=user_id,
        title="профильный прогон",
        archived=False,
        mode="agent",
        agent_profile_id=agent_profile_id,
        stop_requested=stop_requested,
    )
    db_session.add(conversation)
    await db_session.flush()
    return conversation.id


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
    conversation_id: str | None = None,
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
        conversation_id=conversation_id,
        rate_limiter=None,
        trace_ctx=TraceContext(trace_id="trace-profiles", workspace_id=workspace_id),
        max_steps=5,
        max_tokens_per_run=100_000,
        tools_registry=registry,
    )


def _patch_model(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any]],
    *,
    on_call: Any = None,
) -> dict[str, int]:
    """Подменяет complete_tools; ``on_call`` вызывается внутри ответа.

    ``on_call`` — точка, где тест ставит флаг остановки ПОСЛЕ начала вызова
    модели: так проверяется, что текущий шаг не обрывается.
    """
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
        if on_call is not None:
            await on_call(calls["n"])
        return responses[idx]

    monkeypatch.setattr(ProviderClient, "complete_tools", _stub)
    return calls


# ---------------------------------------------------------------------------
# решение 7: остановка между шагами
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_requested_before_run_stops_without_model_call(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Флаг до прогона: ни одного вызова модели, расход не начислен."""
    pytest.importorskip("langgraph")
    workspace_id, user_id, model, provider = await _seed(db_session)
    user = await db_session.get(User, user_id)
    assert user is not None
    conversation_id = await _seed_conversation(
        db_session, workspace_id, user_id, stop_requested=True
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
        conversation_id=conversation_id,
    )
    calls = _patch_model(monkeypatch, [_final_payload()])

    result = await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])

    assert result.stopped is True
    assert calls["n"] == 0
    assert result.model_calls == 0
    assert result.tokens_in == 0
    assert result.tokens_out == 0
    assert result.content == STOPPED_CONTENT
    assert [step.kind for step in result.steps] == ["stop"]
    # Флаг потреблён: следующий прогон стартует с чистого состояния.
    conversation = await db_session.get(Conversation, conversation_id)
    assert conversation is not None
    assert conversation.stop_requested is False
    events = (await db_session.execute(select(UsageEvent))).scalars().all()
    assert events == []


@pytest.mark.asyncio
async def test_stop_during_model_call_does_not_abort_current_step(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Решение 7: «стоп» во время вызова модели — шаг дорабатывает.

    Первый вызов модели завершается штатно: расход записан
    побиллингово, шаг попал в ленту. Остановка срабатывает перед партией
    инструментов — то есть МЕЖДУ шагами, а не внутри вызова.
    """
    pytest.importorskip("langgraph")
    workspace_id, user_id, model, provider = await _seed(db_session)
    user = await db_session.get(User, user_id)
    assert user is not None
    conversation_id = await _seed_conversation(db_session, workspace_id, user_id)
    cfg = _make_config(
        db_session,
        test_settings,
        tmp_path,
        workspace_id,
        user,
        model,
        provider,
        vector_stores,
        conversation_id=conversation_id,
    )

    async def _request_stop(call_number: int) -> None:
        if call_number == 1:
            await db_session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(stop_requested=True)
            )
            await db_session.commit()

    calls = _patch_model(
        monkeypatch,
        [_tool_call_payload(), _final_payload()],
        on_call=_request_stop,
    )

    result = await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])

    assert result.stopped is True
    # Текущий шаг доработан: вызов модели состоялся и оплачен.
    assert calls["n"] == 1
    assert result.model_calls == 1
    assert result.tokens_in == 5
    assert result.tokens_out == 2
    kinds = [step.kind for step in result.steps]
    assert kinds == ["model", "stop"]
    assert result.content == STOPPED_CONTENT
    events = (await db_session.execute(select(UsageEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].status == "ok"
    conversation = await db_session.get(Conversation, conversation_id)
    assert conversation is not None
    assert conversation.stop_requested is False


@pytest.mark.asyncio
async def test_stop_writes_dual_audit_and_span(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Дуальный аудит факта остановки (решение 7)."""
    pytest.importorskip("langgraph")
    workspace_id, user_id, model, provider = await _seed(db_session)
    user = await db_session.get(User, user_id)
    assert user is not None
    conversation_id = await _seed_conversation(
        db_session, workspace_id, user_id, stop_requested=True
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
        conversation_id=conversation_id,
    )
    _patch_model(monkeypatch, [_final_payload()])

    result = await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])
    assert result.stopped is True

    audits = (await db_session.execute(select(AuditLog))).scalars().all()
    stop_audits = [a for a in audits if a.action == "agent.conversation.stopped"]
    assert len(stop_audits) == 1
    assert stop_audits[0].object_type == "conversation"
    assert stop_audits[0].object_id == conversation_id
    assert stop_audits[0].meta is not None
    assert stop_audits[0].meta["model_calls"] == 0
    # Содержимого переписки в записи нет: только счётчики прогона.
    assert set(stop_audits[0].meta) == {"model_calls", "tokens_in", "tokens_out"}

    span_names = [rec.name for rec in cfg.trace_ctx.spans]
    assert "agent.conversation.stopped" in span_names


@pytest.mark.asyncio
async def test_run_without_conversation_is_never_stopped(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Без диалога флага нет: цикл не делает лишних запросов к базе."""
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
        conversation_id=None,
    )
    calls = _patch_model(monkeypatch, [_final_payload()])

    assert await is_stop_requested(db_session, None) is False
    result = await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])

    assert result.stopped is False
    assert result.content == _FINAL_ANSWER
    assert calls["n"] == 1
    await consume_stop_request(db_session, None)  # no-op без исключения


@pytest.mark.asyncio
async def test_completed_run_leaves_flag_clean(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Штатный прогон не трогает флаг остановки."""
    pytest.importorskip("langgraph")
    workspace_id, user_id, model, provider = await _seed(db_session)
    user = await db_session.get(User, user_id)
    assert user is not None
    conversation_id = await _seed_conversation(db_session, workspace_id, user_id)
    cfg = _make_config(
        db_session,
        test_settings,
        tmp_path,
        workspace_id,
        user,
        model,
        provider,
        vector_stores,
        conversation_id=conversation_id,
    )
    _patch_model(monkeypatch, [_tool_call_payload(), _final_payload()])

    result = await run_agent_loop(cfg, [{"role": "user", "content": "вопрос"}])

    assert result.stopped is False
    assert result.content == _FINAL_ANSWER
    assert "stop" not in [step.kind for step in result.steps]
    conversation = await db_session.get(Conversation, conversation_id)
    assert conversation is not None
    assert conversation.stop_requested is False


# ---------------------------------------------------------------------------
# решения 1 и 2: разрешение профиля для прогона
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_profile_for_run_fail_closed(
    db_session: AsyncSession,
) -> None:
    """Призрак и отключённый профиль — ``AgentProfileNotAvailable`` (400)."""
    workspace_id, user_id, model, _provider = await _seed(db_session)
    enabled = AgentProfile(
        workspace_id=workspace_id,
        name="включённый",
        description="",
        model_id=model.id,
        enabled=True,
        created_by=user_id,
    )
    disabled = AgentProfile(
        workspace_id=workspace_id,
        name="отключённый",
        description="",
        model_id=model.id,
        enabled=False,
        created_by=user_id,
    )
    db_session.add_all([enabled, disabled])
    await db_session.flush()

    resolved = await resolve_profile_for_run(db_session, workspace_id, enabled.id)
    assert resolved.id == enabled.id
    assert resolved.model_id == model.id
    assert resolved.skill_id is None

    with pytest.raises(AgentProfileNotAvailable) as ghost_info:
        await resolve_profile_for_run(db_session, workspace_id, "ghost")
    assert (ghost_info.value.constraint or {})["agent_profile_id"] == "ghost"
    assert ghost_info.value.status_code == 400
    assert ghost_info.value.error_code == "agent_profile_not_available"

    with pytest.raises(AgentProfileNotAvailable):
        await resolve_profile_for_run(db_session, workspace_id, disabled.id)

    # Чужая рабочая область профиль не видит: изоляция по workspace_id.
    with pytest.raises(AgentProfileNotAvailable):
        await resolve_profile_for_run(db_session, "other-workspace", enabled.id)


@pytest.mark.asyncio
async def test_is_stop_requested_reads_fresh_value(
    db_session: AsyncSession,
) -> None:
    """Флаг читается из базы заново: его ставит другой запрос."""
    workspace_id, user_id, _model, _provider = await _seed(db_session)
    conversation_id = await _seed_conversation(db_session, workspace_id, user_id)

    assert await is_stop_requested(db_session, conversation_id) is False

    await db_session.execute(
        update(Conversation).where(Conversation.id == conversation_id).values(stop_requested=True)
    )
    await db_session.commit()
    assert await is_stop_requested(db_session, conversation_id) is True

    await consume_stop_request(db_session, conversation_id)
    assert await is_stop_requested(db_session, conversation_id) is False
