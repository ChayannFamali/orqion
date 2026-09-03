"""Т-502: инструмент поиска агента — гарантии ADR-21 (пункт 7).

Отказ по ``policy.corpora`` получается тем же вызовом ``enforce``, что и
в обычном чате: тот же текст и перечень недоступных корпусов (тихая
фильтрация подмножества исключена построением). Компактный факт вызова
(разрешено/отклонено, класс данных) пишется в журнал аудита. Позитивный
путь с реальными фрагментами покрывают интеграционные тесты эндпоинта.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.agent.tools import (
    AGENT_TOOL_SPECS,
    SEARCH_CORPUS_SPEC,
    ToolRunContext,
    execute_search_corpus,
    get_tool_spec,
    openai_tool_schemas,
)
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.models import AuditLog, Corpus, Model, Provider, Role, User, Workspace
from app.policy.models import Policy
from app.rag.vector_store import EMBEDDING_DIM, SQLiteVectorStore
from app.trace.service import TraceContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def vector_stores() -> AsyncIterator[list[SQLiteVectorStore]]:
    """Хранилища, созданные тестом, закрываются после него (иначе процесс виснет)."""
    stores: list[SQLiteVectorStore] = []
    yield stores
    for store in stores:
        await store.close()


async def _seed_base(db_session: AsyncSession) -> tuple[str, str, Model, Provider]:
    ws = Workspace(name="agent-tools-test")
    db_session.add(ws)
    await db_session.flush()
    role = Role(
        workspace_id=ws.id,
        name="agent-tools-role",
        is_builtin=False,
        policy={"models": ["*"], "corpora": ["allowed-*"]},
    )
    db_session.add(role)
    await db_session.flush()
    user = User(
        workspace_id=ws.id,
        email="agent-tools@orqion.local",
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
        alias="local/agent-test",
        upstream_name="upstream",
        locality="local",
        enabled=True,
        supports_tools=True,
    )
    db_session.add(model)
    await db_session.flush()
    return ws.id, user.id, model, provider


def _make_tctx(
    db_session: AsyncSession,
    settings: Settings,
    tmp_path: Any,
    workspace_id: str,
    user_id: str,
    model: Model,
    provider: Provider,
    policy: Policy,
    corpus_names: list[str],
    stores: list[SQLiteVectorStore],
) -> ToolRunContext:
    embedding_backend = AsyncMock()
    # Вектор запроса: единичный — детерминированно, без нуля (нормировка).
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = 1.0
    embedding_backend.embed.return_value = [vector]
    store = SQLiteVectorStore(str(tmp_path / "vec.db"))
    stores.append(store)
    return ToolRunContext(
        session=db_session,
        settings=settings,
        vector_store=store,
        embedding_backend=embedding_backend,
        secret_key="test-secret",
        workspace_id=workspace_id,
        user_id=user_id,
        policy=policy,
        corpora=[
            Corpus(name=name, workspace_id=workspace_id, data_class="К0") for name in corpus_names
        ],
        corpus_names=corpus_names,
        corpus_data_class="К0",
        model=model,
        provider=provider,
        trace_ctx=TraceContext(trace_id="trace-tools", workspace_id=workspace_id),
        conversation_id=None,
    )


@pytest.mark.asyncio
async def test_search_denied_by_policy_with_listing(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Отказ — тот же, что в чате: причина и перечень недоступных корпусов."""
    workspace_id, user_id, model, provider = await _seed_base(db_session)
    policy = Policy(models=["*"], corpora=["allowed-*"])
    tctx = _make_tctx(
        db_session,
        test_settings,
        tmp_path,
        workspace_id,
        user_id,
        model,
        provider,
        policy,
        corpus_names=["secret-docs"],
        stores=vector_stores,
    )

    outcome = await execute_search_corpus("вопрос", tctx)

    assert outcome.decision == "deny"
    # Перечень недоступных корпусов и разрешённые шаблоны — в тексте.
    assert "secret-docs" in outcome.text
    assert "allowed-*" in outcome.text
    assert outcome.sources == []

    # Компактный факт отказа — в журнале аудита бессрочно.
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    tool_rows = [r for r in rows if r.action == "agent.tool.search_corpus"]
    assert len(tool_rows) == 1
    meta = tool_rows[0].meta
    assert meta is not None
    assert meta["decision"] == "deny"
    assert meta["data_class"] == "К0"
    assert meta["corpus_names"] == ["secret-docs"]


@pytest.mark.asyncio
async def test_search_allowed_writes_audit_allow(
    db_session: AsyncSession,
    test_settings: Settings,
    tmp_path: Any,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Разрешённый вызов: поиск проходит (пустой корпус — честное «не найдено»),
    факт ``allow`` фиксируется в журнале аудита."""
    workspace_id, user_id, model, provider = await _seed_base(db_session)
    policy = Policy(models=["*"], corpora=["*"])
    tctx = _make_tctx(
        db_session,
        test_settings,
        tmp_path,
        workspace_id,
        user_id,
        model,
        provider,
        policy,
        corpus_names=["allowed-corpus"],
        stores=vector_stores,
    )

    outcome = await execute_search_corpus("вопрос", tctx)

    assert outcome.decision == "allow"
    assert "не найдено" in outcome.text

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    tool_rows = [r for r in rows if r.action == "agent.tool.search_corpus"]
    assert len(tool_rows) == 1
    meta = tool_rows[0].meta
    assert meta is not None
    assert meta["decision"] == "allow"


def test_tool_registry() -> None:
    """MVP-реестр: единственный встроенный инструмент — поиск, не деструктивный."""
    assert get_tool_spec("search_corpus", AGENT_TOOL_SPECS) is SEARCH_CORPUS_SPEC
    assert SEARCH_CORPUS_SPEC.destructive is False
    assert get_tool_spec("unknown-tool", AGENT_TOOL_SPECS) is None

    schemas = openai_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["type"] == "function"
    function = schemas[0]["function"]
    assert function["name"] == "search_corpus"
    assert "query" in function["parameters"]["properties"]
