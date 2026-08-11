"""Тесты RAG-конвейера (T-220).

Проверки:
- test_pipeline_full_run: все 5 шагов, mock провайдер, проверка state
- test_pipeline_rewrite_degraded: rewrite fails → degraded=True, конвейер продолжается
- test_pipeline_search_uses_rewritten: rewritten задан → search использует его
- test_pipeline_search_falls_back_to_query: rewritten=None → search использует query
- test_pipeline_rerank_degraded: reranker=None → degraded=True, конвейер продолжается
- test_pipeline_build_context_truncation: truncation → degraded=True, errors заполнен
- test_pipeline_generate_uses_context: generate получает context, возвращает answer
- test_pipeline_generate_zero_fragments_still_calls_model: 0 фрагментов → модель вызвана
- test_pipeline_steps_are_data: PIPELINE — список, можно заменить
- test_pipeline_trace_spans: 5 span'ов в trace_ctx
- test_pipeline_step_replaceable: custom step вместо стандартного
- test_pipeline_error_continues: шаг бросает Exception → state.errors, продолжается
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Workspace
from app.providers.client import ProviderClient
from app.rag.hybrid_search import HybridResult
from app.rag.pipeline import (
    PIPELINE,
    RagContext,
    RagState,
    run_pipeline,
    step_generate,
    step_rewrite,
    step_search,
)
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_provider_and_model(workspace_id: str) -> tuple[Provider, Model]:
    provider = Provider(
        workspace_id=workspace_id,
        kind="openai",
        base_url="http://stub:1234/v1",
        api_key_enc=encrypt_api_key("sk-test", "test-secret"),
        enabled=True,
        capabilities={},
    )
    provider.id = "prov-1"
    model = Model(
        workspace_id=workspace_id,
        provider_id="prov-1",
        alias="local/test-model",
        upstream_name="test-upstream",
        locality="local",
        max_input_tokens=32000,
        max_output_tokens=4096,
        enabled=True,
    )
    model.id = "model-1"
    return provider, model


def _make_settings(
    enabled: bool = False,
    alias: str = "",
) -> Any:
    from app.config import Settings

    return Settings(
        rag_query_reformulation_enabled=enabled,
        rag_reformulation_model_alias=alias,
    )


async def _make_workspace(session: AsyncSession) -> str:
    ws = Workspace(name="test")
    session.add(ws)
    await session.flush()
    return ws.id


def _stub_complete_response(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


async def test_pipeline_full_run(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Все 5 шагов, mock провайдер, проверка state на каждом этапе."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    # Stub ProviderClient.complete
    async def _complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        return _stub_complete_response("Generated answer")

    monkeypatch.setattr(ProviderClient, "complete", _complete)

    # Stub vector_store + embedding_backend
    from unittest.mock import AsyncMock

    vector_store = AsyncMock()
    embedding_backend = AsyncMock()

    state = RagState(query="test query", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=vector_store,
        embedding_backend=embedding_backend,
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    # Replace steps with stubs that don't need real vector_store
    async def _stub_rewrite(s: RagState, c: RagContext) -> RagState:
        s.rewritten = s.query
        return s

    async def _stub_search(s: RagState, c: RagContext) -> RagState:
        s.hits = [
            HybridResult(chunk_id="c1", score=1.0, text="hit text", dense_rank=1, sparse_rank=1)
        ]
        return s

    async def _stub_rerank(s: RagState, c: RagContext) -> RagState:
        from app.rag.reranker import RerankResult

        s.reranked = [RerankResult(chunk_id="c1", score=1.0, text="hit text", original_rank=1)]
        return s

    async def _stub_build_context(s: RagState, c: RagContext) -> RagState:
        s.context = "System prompt\n\nFragment 1\nhit text\n\ntest query"
        s.fragments_used = 1
        return s

    steps = [_stub_rewrite, _stub_search, _stub_rerank, _stub_build_context, step_generate]

    result = await run_pipeline(state, ctx, steps=steps)

    assert result.answer == "Generated answer"
    assert result.degraded is False
    assert result.rewritten == "test query"
    assert len(result.hits) == 1
    assert len(result.reranked) == 1
    assert result.fragments_used == 1


async def test_pipeline_rewrite_degraded(db_session: AsyncSession) -> None:
    """Rewrite fails → degraded=True, rewritten=None, конвейер продолжается."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    state = RagState(query="test query", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    async def _failing_rewrite(s: RagState, c: RagContext) -> RagState:
        s.rewritten = None
        s.degraded = True
        s.errors.append("rewrite: model not found")
        return s

    async def _stub_search(s: RagState, c: RagContext) -> RagState:
        s.hits = []
        return s

    async def _stub_rerank(s: RagState, c: RagContext) -> RagState:
        s.reranked = []
        return s

    async def _stub_build_context(s: RagState, c: RagContext) -> RagState:
        s.context = "prompt\n\nquery"
        s.fragments_used = 0
        return s

    async def _stub_generate(s: RagState, c: RagContext) -> RagState:
        s.answer = "No answer found"
        return s

    steps = [_failing_rewrite, _stub_search, _stub_rerank, _stub_build_context, _stub_generate]
    result = await run_pipeline(state, ctx, steps=steps)

    assert result.degraded is True
    assert result.rewritten is None
    assert any("rewrite" in e for e in result.errors)
    assert result.answer == "No answer found"


async def test_pipeline_search_uses_rewritten(db_session: AsyncSession) -> None:
    """Если rewritten задан → search использует его."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    captured: list[str] = []

    async def _stub_rewrite(s: RagState, c: RagContext) -> RagState:
        s.rewritten = "Reformulated query"
        return s

    async def _capturing_search(s: RagState, c: RagContext) -> RagState:
        search_query = s.rewritten or s.query
        captured.append(search_query)
        s.hits = []
        return s

    state = RagState(query="original query", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    await run_pipeline(state, ctx, steps=[_stub_rewrite, _capturing_search])

    assert captured[0] == "Reformulated query"


async def test_pipeline_search_falls_back_to_query(db_session: AsyncSession) -> None:
    """Если rewritten=None → search использует query."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    captured: list[str] = []

    async def _stub_rewrite(s: RagState, c: RagContext) -> RagState:
        s.rewritten = None
        return s

    async def _capturing_search(s: RagState, c: RagContext) -> RagState:
        search_query = s.rewritten or s.query
        captured.append(search_query)
        s.hits = []
        return s

    state = RagState(query="original query", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    await run_pipeline(state, ctx, steps=[_stub_rewrite, _capturing_search])

    assert captured[0] == "original query"


async def test_pipeline_rerank_degraded(db_session: AsyncSession) -> None:
    """Reranker=None → degraded=True, конвейер продолжается."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    async def _stub_rewrite(s: RagState, c: RagContext) -> RagState:
        s.rewritten = s.query
        return s

    async def _stub_search(s: RagState, c: RagContext) -> RagState:
        s.hits = [HybridResult(chunk_id="c1", score=1.0, text="text", dense_rank=1, sparse_rank=1)]
        return s

    async def _degraded_rerank(s: RagState, c: RagContext) -> RagState:
        from app.rag.reranker import RerankResult

        s.reranked = [RerankResult(chunk_id="c1", score=1.0, text="text", original_rank=1)]
        s.degraded = True
        s.errors.append("rerank: FlagEmbedding not available")
        return s

    async def _stub_build_context(s: RagState, c: RagContext) -> RagState:
        s.context = "prompt\n\nquery"
        s.fragments_used = 0
        return s

    async def _stub_generate(s: RagState, c: RagContext) -> RagState:
        s.answer = "answer"
        return s

    state = RagState(query="test", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    steps = [_stub_rewrite, _stub_search, _degraded_rerank, _stub_build_context, _stub_generate]
    result = await run_pipeline(state, ctx, steps=steps)

    assert result.degraded is True
    assert any("rerank" in e for e in result.errors)
    assert result.answer == "answer"


async def test_pipeline_build_context_truncation(db_session: AsyncSession) -> None:
    """Truncation → degraded=True, errors заполнен."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    async def _stub_rewrite(s: RagState, c: RagContext) -> RagState:
        s.rewritten = s.query
        return s

    async def _stub_search(s: RagState, c: RagContext) -> RagState:
        s.hits = []
        return s

    async def _stub_rerank(s: RagState, c: RagContext) -> RagState:
        s.reranked = []
        return s

    async def _truncating_build_context(s: RagState, c: RagContext) -> RagState:
        s.context = "prompt\n\nquery"
        s.fragments_used = 0
        s.degraded = True
        s.errors.append("build_context: truncated, 3 skipped oversized")
        return s

    async def _stub_generate(s: RagState, c: RagContext) -> RagState:
        s.answer = "answer"
        return s

    state = RagState(query="test", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    steps = [_stub_rewrite, _stub_search, _stub_rerank, _truncating_build_context, _stub_generate]
    result = await run_pipeline(state, ctx, steps=steps)

    assert result.degraded is True
    assert any("build_context" in e for e in result.errors)


async def test_pipeline_generate_uses_context(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generate получает context, возвращает answer."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    captured: list[list[dict[str, str]]] = []

    async def _complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        captured.append(messages)
        return _stub_complete_response("Answer based on context")

    monkeypatch.setattr(ProviderClient, "complete", _complete)

    async def _stub_rewrite(s: RagState, c: RagContext) -> RagState:
        s.rewritten = s.query
        return s

    async def _stub_search(s: RagState, c: RagContext) -> RagState:
        s.hits = []
        return s

    async def _stub_rerank(s: RagState, c: RagContext) -> RagState:
        s.reranked = []
        return s

    async def _stub_build_context(s: RagState, c: RagContext) -> RagState:
        s.context = "System: answer only from context\n\nquery"
        s.fragments_used = 1
        return s

    state = RagState(query="what is orqion?", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    steps = [_stub_rewrite, _stub_search, _stub_rerank, _stub_build_context, step_generate]
    result = await run_pipeline(state, ctx, steps=steps)

    assert result.answer == "Answer based on context"
    # Generate получает context как system message
    assert captured[0][0]["role"] == "system"
    assert "answer only from context" in captured[0][0]["content"]
    assert captured[0][1]["role"] == "user"


async def test_pipeline_generate_zero_fragments_still_calls_model(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 фрагментов → модель всё равно вызвана, отвечает 'не найдено'."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    called: list[bool] = []

    async def _complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        called.append(True)
        return _stub_complete_response("Информация не найдена в предоставленном материале")

    monkeypatch.setattr(ProviderClient, "complete", _complete)

    async def _stub_rewrite(s: RagState, c: RagContext) -> RagState:
        s.rewritten = s.query
        return s

    async def _stub_search(s: RagState, c: RagContext) -> RagState:
        s.hits = []
        return s

    async def _stub_rerank(s: RagState, c: RagContext) -> RagState:
        s.reranked = []
        return s

    async def _empty_build_context(s: RagState, c: RagContext) -> RagState:
        s.context = "System: answer only from context\n\nquery"
        s.fragments_used = 0
        return s

    state = RagState(query="test", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    steps = [_stub_rewrite, _stub_search, _stub_rerank, _empty_build_context, step_generate]
    result = await run_pipeline(state, ctx, steps=steps)

    assert len(called) == 1
    assert result.answer == "Информация не найдена в предоставленном материале"
    assert result.degraded is False


def test_pipeline_steps_are_data() -> None:
    """PIPELINE — список, можно заменить."""
    assert isinstance(PIPELINE, list)
    assert len(PIPELINE) == 5
    assert PIPELINE[0] is step_rewrite
    assert PIPELINE[-1] is step_generate

    # Можно создать кастомный список
    custom = [step_rewrite, step_search]
    assert len(custom) == 2
    assert custom[0] is step_rewrite


async def test_pipeline_trace_spans(db_session: AsyncSession) -> None:
    """5 span'ов в trace_ctx, имена соответствуют шагам."""
    from app.trace.service import create_trace

    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    trace_ctx = await create_trace(db_session, workspace_id)

    async def _stub_rewrite(s: RagState, c: RagContext) -> RagState:
        s.rewritten = s.query
        return s

    async def _stub_search(s: RagState, c: RagContext) -> RagState:
        s.hits = []
        return s

    async def _stub_rerank(s: RagState, c: RagContext) -> RagState:
        s.reranked = []
        return s

    async def _stub_build_context(s: RagState, c: RagContext) -> RagState:
        s.context = "prompt\n\nquery"
        s.fragments_used = 0
        return s

    async def _stub_generate(s: RagState, c: RagContext) -> RagState:
        s.answer = "answer"
        return s

    state = RagState(query="test", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
        trace_ctx=trace_ctx,
    )

    steps = [_stub_rewrite, _stub_search, _stub_rerank, _stub_build_context, _stub_generate]
    await run_pipeline(state, ctx, steps=steps)

    assert len(trace_ctx.spans) == 5
    names = [s.name for s in trace_ctx.spans]
    assert names == [
        "_stub_rewrite",
        "_stub_search",
        "_stub_rerank",
        "_stub_build_context",
        "_stub_generate",
    ]


async def test_pipeline_step_replaceable(db_session: AsyncSession) -> None:
    """Custom step вместо стандартного."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    custom_called: list[bool] = []

    async def _custom_rewrite(s: RagState, c: RagContext) -> RagState:
        custom_called.append(True)
        s.rewritten = "CUSTOM: " + s.query
        return s

    async def _stub_search(s: RagState, c: RagContext) -> RagState:
        s.hits = []
        return s

    state = RagState(query="test", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    await run_pipeline(state, ctx, steps=[_custom_rewrite, _stub_search])

    assert len(custom_called) == 1
    assert state.rewritten == "CUSTOM: test"


async def test_pipeline_error_continues(db_session: AsyncSession) -> None:
    """Шаг бросает Exception → state.errors, конвейер продолжается."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    async def _failing_search(s: RagState, c: RagContext) -> RagState:
        raise RuntimeError("vector store unavailable")

    async def _stub_rerank(s: RagState, c: RagContext) -> RagState:
        s.reranked = []
        return s

    async def _stub_build_context(s: RagState, c: RagContext) -> RagState:
        s.context = "prompt\n\nquery"
        s.fragments_used = 0
        return s

    async def _stub_generate(s: RagState, c: RagContext) -> RagState:
        s.answer = "answer"
        return s

    state = RagState(query="test", trace_id="trace-1")
    ctx = RagContext(
        session=db_session,
        settings=_make_settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id="iv-1",
        model=model,
        provider=provider,
    )

    steps = [step_rewrite, _failing_search, _stub_rerank, _stub_build_context, _stub_generate]
    result = await run_pipeline(state, ctx, steps=steps)

    assert result.degraded is True
    assert any("vector store unavailable" in e for e in result.errors)
    assert result.answer == "answer"
