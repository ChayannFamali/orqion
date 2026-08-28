"""Т-506: настройки RAG-поиска в конвейере.

Проверки:
- apply_search_settings: порог=0 — сентинел «выключен» (фильтрация не
  выполняется); включённый порог сравнивает скор реранкера в процентах
  (граница >=); максимум фрагментов — срез сверху.
- step_rerank применяет порог/максимум ПОСЛЕ реранкера.
- Мульти-корпусный режим (двухуровневый RRF из T-439): порог/максимум
  накладываются на объединённый список после единого прохода реранкера,
  независимо от числа корпусов.
- Деградация реранкера: порог не применяется (другая шкала скора), факт
  фиксируется; максимум по-прежнему ограничивает.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from app.db.models import Model, Provider, RagSettings, Workspace
from app.rag.hybrid_search import HybridResult, HybridSearchOutput
from app.rag.pipeline import (
    RagContext,
    RagState,
    apply_search_settings,
    step_rerank,
    step_search,
)
from app.rag.reranker import RerankOutput, RerankResult
from sqlalchemy.ext.asyncio import AsyncSession


def _rr(chunk_id: str, score: float) -> RerankResult:
    return RerankResult(chunk_id=chunk_id, score=score, text=f"text-{chunk_id}", original_rank=1)


# ---------------------------------------------------------------------------
# apply_search_settings — чистая функция
# ---------------------------------------------------------------------------


def test_apply_search_settings_threshold_zero_is_sentinel() -> None:
    """Порог=0 — фильтр выключен: ничего не отсекается даже при скоре 0."""
    results = [_rr("a", 0.0), _rr("b", 0.01), _rr("c", 0.9)]
    kept = apply_search_settings(results, threshold_percent=0, max_fragments=8)
    assert [r.chunk_id for r in kept] == ["a", "b", "c"]


def test_apply_search_settings_threshold_filters_in_percent() -> None:
    """Скор реранкера 0–1 сравнивается в процентах, граница включительная."""
    results = [_rr("hi", 0.9), _rr("edge", 0.5), _rr("low", 0.49)]
    kept = apply_search_settings(results, threshold_percent=50, max_fragments=8)
    # 0.5*100 == 50 проходит (>=), 0.49*100 == 49 не проходит
    assert [r.chunk_id for r in kept] == ["hi", "edge"]


def test_apply_search_settings_max_slices_top() -> None:
    results = [_rr(str(i), 0.9) for i in range(8)]
    kept = apply_search_settings(results, threshold_percent=0, max_fragments=3)
    assert len(kept) == 3


def test_apply_search_settings_threshold_and_max_combined() -> None:
    results = [_rr("a", 0.9), _rr("b", 0.7), _rr("c", 0.6), _rr("d", 0.3)]
    kept = apply_search_settings(results, threshold_percent=50, max_fragments=2)
    assert [r.chunk_id for r in kept] == ["a", "b"]


# ---------------------------------------------------------------------------
# step_rerank — интеграция с настройками
# ---------------------------------------------------------------------------


async def _make_workspace(session: AsyncSession) -> str:
    ws = Workspace(name="test")
    session.add(ws)
    await session.flush()
    return ws.id


def _make_provider_and_model(workspace_id: str) -> tuple[Provider, Model]:
    from app.crypto.service import encrypt_api_key

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


def _make_ctx(
    session: AsyncSession,
    workspace_id: str,
    provider: Provider,
    model: Model,
    index_version_ids: list[str],
) -> RagContext:
    from app.config import Settings

    return RagContext(
        session=session,
        settings=Settings(),
        vector_store=AsyncMock(),
        embedding_backend=AsyncMock(),
        secret_key="test-secret",
        workspace_id=workspace_id,
        index_version_id=index_version_ids[0],
        model=model,
        provider=provider,
        reranker=object(),  # не None — create_reranker() не вызывается
        index_version_ids=index_version_ids,
    )


def _patch_rerank_output(
    monkeypatch: pytest.MonkeyPatch,
    results: list[RerankResult],
    degraded: bool = False,
) -> None:
    """Подменяет rerank в пространстве имён pipeline."""
    import app.rag.pipeline as pipeline_mod

    async def _fake_rerank(
        query: str,
        candidates: list[HybridResult],
        reranker: object,
        top_k: int = 8,
    ) -> RerankOutput:
        return RerankOutput(results=results, degraded=degraded, duration_ms=1.0, error=None)

    monkeypatch.setattr(pipeline_mod, "rerank", _fake_rerank)


async def test_step_rerank_applies_threshold_and_max(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Порог и максимум применяются после реранкера (реранкер отработал)."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    db_session.add(RagSettings(workspace_id=workspace_id, relevance_threshold=50, max_fragments=2))
    await db_session.flush()

    _patch_rerank_output(
        monkeypatch,
        [_rr("a", 0.9), _rr("b", 0.7), _rr("c", 0.6), _rr("d", 0.3)],
    )

    ctx = _make_ctx(db_session, workspace_id, provider, model, ["iv-1"])
    state = RagState(query="q", trace_id="t-1")
    state.hits = [
        HybridResult(chunk_id=str(i), score=1.0, text="t", dense_rank=1, sparse_rank=1)
        for i in range(4)
    ]

    result = await step_rerank(state, ctx)

    assert [r.chunk_id for r in result.reranked] == ["a", "b"]
    assert result.search_threshold_percent == 50
    assert result.search_max_fragments == 2
    assert result.search_threshold_applied is True
    assert result.search_threshold_skipped_degraded is False


async def test_step_rerank_defaults_without_settings_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без строки настроек — дефолты: порог выключен, максимум 8."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    await db_session.flush()

    _patch_rerank_output(monkeypatch, [_rr("a", 0.01), _rr("b", 0.9)])

    ctx = _make_ctx(db_session, workspace_id, provider, model, ["iv-1"])
    state = RagState(query="q", trace_id="t-1")
    state.hits = [HybridResult(chunk_id="a", score=1.0, text="t", dense_rank=1, sparse_rank=1)]

    result = await step_rerank(state, ctx)

    # порог=0 — ничего не отсеяно, даже скор 0.01
    assert [r.chunk_id for r in result.reranked] == ["a", "b"]
    assert result.search_threshold_applied is False
    assert result.search_max_fragments == 8


async def test_step_rerank_degraded_skips_threshold_keeps_max(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Деградация: порог пропускается (шкала РРФ), максимум действует."""
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    db_session.add(RagSettings(workspace_id=workspace_id, relevance_threshold=50, max_fragments=2))
    await db_session.flush()

    # В деградации скоры — РРФ (малые значения); порог к ним неприменим.
    _patch_rerank_output(
        monkeypatch,
        [_rr("a", 0.02), _rr("b", 0.015), _rr("c", 0.01)],
        degraded=True,
    )

    ctx = _make_ctx(db_session, workspace_id, provider, model, ["iv-1"])
    state = RagState(query="q", trace_id="t-1")
    state.hits = [HybridResult(chunk_id="a", score=1.0, text="t", dense_rank=1, sparse_rank=1)]

    result = await step_rerank(state, ctx)

    assert [r.chunk_id for r in result.reranked] == ["a", "b"]  # срезано максимумом
    assert result.search_threshold_skipped_degraded is True
    assert result.search_threshold_applied is False
    assert result.degraded is True


async def test_multi_corpus_threshold_after_two_level_rrf(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Мульти-корпусный режим: порог/максимум накладываются после
    двухуровневого RRF (T-439) и единого прохода реранкера.

    Шаг поиска сливает результаты двух корпусов вторым RRF, затем
    реранкер проходит по объединённому списку; настройки применяются к
    итоговому общему списку — не к каждому корпусу отдельно.
    """
    workspace_id = await _make_workspace(db_session)
    provider, model = _make_provider_and_model(workspace_id)
    db_session.add(provider)
    db_session.add(model)
    db_session.add(RagSettings(workspace_id=workspace_id, relevance_threshold=50, max_fragments=2))
    await db_session.flush()

    import app.rag.pipeline as pipeline_mod

    # Каждый корпус отдаёт своего кандидата; второй RRF их объединяет.
    async def _fake_hybrid_search(
        vector_store: object,
        embedding_backend: object,
        index_version_id: str,
        query: str,
        k: int = 50,
    ) -> HybridSearchOutput:
        chunk_id = "c-a" if index_version_id == "iv-a" else "c-b"
        merged = [
            HybridResult(
                chunk_id=chunk_id, score=1.0, text=f"text-{chunk_id}", dense_rank=1, sparse_rank=1
            )
        ]
        return HybridSearchOutput(dense_hits=[], sparse_hits=[], merged=merged)

    monkeypatch.setattr(pipeline_mod, "hybrid_search", _fake_hybrid_search)

    # Реранкер оценивает объединённый список: один выше порога, один ниже.
    _patch_rerank_output(
        monkeypatch,
        [_rr("c-a", 0.8), _rr("c-b", 0.2)],
    )

    ctx = _make_ctx(db_session, workspace_id, provider, model, ["iv-a", "iv-b"])
    state = RagState(query="q", trace_id="t-1")

    state = await step_search(state, ctx)
    # Объединённый список содержит кандидатов обоих корпусов (двухуровневый RRF)
    assert {h.chunk_id for h in state.hits} == {"c-a", "c-b"}

    state = await step_rerank(state, ctx)
    # Порог применён к общему списку: остался только кандидат выше порога
    assert [r.chunk_id for r in state.reranked] == ["c-a"]
    assert state.search_threshold_applied is True
