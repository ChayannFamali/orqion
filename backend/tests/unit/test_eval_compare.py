"""Тесты сравнения прогонов (T-226).

Проверки:
- test_compare_two_runs: сравнение 2 прогонов — дельты метрик
- test_compare_three_runs: 3 прогона — дельты для двух пар
- test_compare_sorted_by_ts: прогоны сортируются по ts, не по порядку в запросе
- test_compare_different_eval_sets_rejected: разные eval_set_id → ValueError
- test_compare_single_run_rejected: 1 прогон → ValueError
- test_compare_empty_metrics: метрики None → пустые дельты
- test_metric_direction: ↑ для positive delta, ↓ для negative, → для zero
- test_run_metadata_extracted: per-run метаданные из pipeline_config
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.db.base import _utcnow
from app.db.models import EvalRun
from app.rag.eval_compare import (
    _metric_direction,
    _run_to_metadata,
    compare_runs,
)

_SENTINEL = object()


def _make_run(
    run_id: str = "run-1",
    eval_set_id: str = "es-1",
    ts: datetime | None = None,
    index_version_id: str | None = "iv-1",
    metrics: dict[str, object] | None = _SENTINEL,  # type: ignore[assignment]
    pipeline: dict[str, object] | None = None,
) -> EvalRun:
    if ts is None:
        ts = _utcnow()
    if metrics is _SENTINEL:
        metrics = {"recall@5": 0.8, "mrr": 0.7}
    if pipeline is None:
        pipeline = {
            "steps": ["rewrite", "search", "rerank", "build_context", "generate"],
            "generate_model_alias": "local/test-model",
            "reranker_enabled": True,
        }
    run = EvalRun(
        workspace_id="ws-1",
        eval_set_id=eval_set_id,
        index_version_id=index_version_id,
        pipeline=pipeline,
        metrics=metrics,
        ts=ts,
    )
    run.id = run_id
    return run


# ---------------------------------------------------------------------------
# compare_runs
# ---------------------------------------------------------------------------


def test_compare_two_runs() -> None:
    """Сравнение 2 прогонов — дельты метрик."""
    earlier = _make_run(
        "r1",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        metrics={"recall@5": 0.6, "mrr": 0.5},
    )
    later = _make_run(
        "r2",
        ts=datetime(2026, 1, 2, tzinfo=UTC),
        metrics={"recall@5": 0.8, "mrr": 0.5},
    )

    comparison = compare_runs([later, earlier])  # переданы в обратном порядке

    # Сортировка по ts: earlier (r1) → later (r2)
    assert comparison.runs[0].run_id == "r1"
    assert comparison.runs[1].run_id == "r2"

    # Дельты: recall@5 improved, mrr unchanged
    recall_delta = [d for d in comparison.deltas if d.metric_name == "recall@5"]
    assert len(recall_delta) == 1
    assert recall_delta[0].delta == 0.2
    assert recall_delta[0].direction == "↑"

    mrr_delta = [d for d in comparison.deltas if d.metric_name == "mrr"]
    assert len(mrr_delta) == 1
    assert mrr_delta[0].delta == 0.0
    assert mrr_delta[0].direction == "→"


def test_compare_three_runs() -> None:
    """3 прогона — дельты для двух пар (r1→r2, r2→r3)."""
    r1 = _make_run("r1", ts=datetime(2026, 1, 1, tzinfo=UTC), metrics={"recall@5": 0.5})
    r2 = _make_run("r2", ts=datetime(2026, 1, 2, tzinfo=UTC), metrics={"recall@5": 0.7})
    r3 = _make_run("r3", ts=datetime(2026, 1, 3, tzinfo=UTC), metrics={"recall@5": 0.6})

    comparison = compare_runs([r1, r2, r3])

    assert len(comparison.runs) == 3
    # Две пары дельт
    recall_deltas = [d for d in comparison.deltas if d.metric_name == "recall@5"]
    assert len(recall_deltas) == 2
    # r1→r2: +0.2 (↑)
    assert recall_deltas[0].delta == 0.2
    assert recall_deltas[0].direction == "↑"
    # r2→r3: -0.1 (↓)
    assert recall_deltas[1].delta == -0.1
    assert recall_deltas[1].direction == "↓"


def test_compare_sorted_by_ts() -> None:
    """Прогоны сортируются по ts — независимо от порядка в запросе."""
    earlier = _make_run("early", ts=datetime(2026, 1, 1, tzinfo=UTC), metrics={"mrr": 0.5})
    later = _make_run("late", ts=datetime(2026, 1, 10, tzinfo=UTC), metrics={"mrr": 0.7})

    # Переданы в обратном порядке
    comparison = compare_runs([later, earlier])

    assert comparison.runs[0].run_id == "early"
    assert comparison.runs[1].run_id == "late"

    # Дельта: +0.2 (↑) — later better
    mrr_deltas = [d for d in comparison.deltas if d.metric_name == "mrr"]
    assert mrr_deltas[0].delta == 0.2
    assert mrr_deltas[0].direction == "↑"


def test_compare_different_eval_sets_rejected() -> None:
    """Разные eval_set_id → ValueError."""
    r1 = _make_run("r1", eval_set_id="es-1")
    r2 = _make_run("r2", eval_set_id="es-2")

    with pytest.raises(ValueError, match="different eval sets"):
        compare_runs([r1, r2])


def test_compare_single_run_rejected() -> None:
    """1 прогон → ValueError."""
    r1 = _make_run("r1")
    with pytest.raises(ValueError, match="at least 2"):
        compare_runs([r1])


def test_compare_empty_metrics() -> None:
    """Метрики None → пустые дельты."""
    r1 = _make_run("r1", ts=datetime(2026, 1, 1, tzinfo=UTC), metrics=None)
    r2 = _make_run("r2", ts=datetime(2026, 1, 2, tzinfo=UTC), metrics=None)

    comparison = compare_runs([r1, r2])
    assert comparison.deltas == []


# ---------------------------------------------------------------------------
# _metric_direction
# ---------------------------------------------------------------------------


def test_metric_direction() -> None:
    """↑ для positive, ↓ для negative, → для zero."""
    assert _metric_direction(0.1) == "↑"
    assert _metric_direction(-0.05) == "↓"
    assert _metric_direction(0.0) == "→"


# ---------------------------------------------------------------------------
# _run_to_metadata
# ---------------------------------------------------------------------------


def test_run_metadata_extracted() -> None:
    """Per-run метаданные из pipeline_config."""
    run = _make_run(
        "r1",
        index_version_id="iv-2",
        pipeline={
            "steps": ["search", "generate"],
            "generate_model_alias": "local/gpt-4",
            "rewrite_model_alias": "local/rewrite-model",
            "reranker_enabled": False,
        },
    )

    meta = _run_to_metadata(run)
    assert meta.run_id == "r1"
    assert meta.index_version_id == "iv-2"
    assert meta.generate_model_alias == "local/gpt-4"
    assert meta.rewrite_model_alias == "local/rewrite-model"
    assert meta.reranker_enabled is False
    assert meta.steps == ["search", "generate"]


def test_run_metadata_defaults() -> None:
    """Метаданные с пустым pipeline_config — дефолты."""
    run = _make_run("r1", pipeline={})
    meta = _run_to_metadata(run)
    assert meta.generate_model_alias == ""
    assert meta.rewrite_model_alias is None
    assert meta.reranker_enabled is True
    assert meta.steps == []
