"""Сравнение прогонов оценки (T-226).

arch.md §8.4, ADR-10: сравнение конфигураций пайплайна, не только метрик.
Приёмка: сравнение запрещено для разных наборов вопросов; разница метрик
представлена явно.

Прогоны сортируются по ts (по возрастанию) перед вычислением дельт —
«↑» всегда значит «стало лучше со временем», а не «в порядке запроса».

EvalComparison включает per-run метаданные (index_version_id, ключевые поля
pipeline_config) рядом с дельтами метрик — для сравнения конфигураций.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.models import EvalRun


@dataclass(frozen=True)
class RunMetadata:
    """Метаданные одного прогона для сравнения конфигураций."""

    run_id: str
    ts: str
    index_version_id: str | None
    generate_model_alias: str
    rewrite_model_alias: str | None
    reranker_enabled: bool
    steps: list[str]


@dataclass(frozen=True)
class MetricDelta:
    """Дельта одной метрики между двумя прогонами."""

    metric_name: str
    earlier_value: float
    later_value: float
    delta: float
    direction: str  # "↑" (better), "↓" (worse), "→" (unchanged)


@dataclass(frozen=True)
class EvalComparison:
    """Результат сравнения прогонов.

    runs отсортированы по ts (по возрастанию).
    deltas — последовательные пары: runs[0] vs runs[1], runs[1] vs runs[2] и т.д.
    """

    eval_set_id: str
    runs: list[RunMetadata]
    deltas: list[MetricDelta]

    def to_dict(self) -> dict[str, object]:
        """Сериализация в dict для API ответа."""
        return {
            "eval_set_id": self.eval_set_id,
            "runs": [
                {
                    "run_id": r.run_id,
                    "ts": r.ts,
                    "index_version_id": r.index_version_id,
                    "generate_model_alias": r.generate_model_alias,
                    "rewrite_model_alias": r.rewrite_model_alias,
                    "reranker_enabled": r.reranker_enabled,
                    "steps": r.steps,
                }
                for r in self.runs
            ],
            "deltas": [
                {
                    "metric_name": d.metric_name,
                    "earlier_value": d.earlier_value,
                    "later_value": d.later_value,
                    "delta": d.delta,
                    "direction": d.direction,
                }
                for d in self.deltas
            ],
        }


def _run_to_metadata(run: EvalRun) -> RunMetadata:
    """Извлекает метаданные из EvalRun.pipeline_config."""
    pipeline = run.pipeline or {}
    steps_raw = pipeline.get("steps", [])
    steps = [str(s) for s in steps_raw] if isinstance(steps_raw, list) else []
    rewrite_raw = pipeline.get("rewrite_model_alias")
    return RunMetadata(
        run_id=run.id,
        ts=run.ts.isoformat() if run.ts else "",
        index_version_id=run.index_version_id,
        generate_model_alias=str(pipeline.get("generate_model_alias", "")),
        rewrite_model_alias=str(rewrite_raw) if rewrite_raw is not None else None,
        reranker_enabled=bool(pipeline.get("reranker_enabled", True)),
        steps=steps,
    )


def _metric_direction(delta: float) -> str:
    """Направление изменения: ↑ (better), ↓ (worse), → (unchanged).

    Для recall@k, MRR, cited_sources_ratio, grounded_refusal_ratio —
    больше = лучше (кроме grounded_refusal_ratio, где тоже больше = лучше,
    т.к. это доля честных отказов, а не галлюцинаций).
    """
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def compare_runs(runs: list[EvalRun]) -> EvalComparison:
    """Сравнивает 2+ прогона.

    Args:
        runs: список EvalRun (2+).

    Returns:
        EvalComparison с per-run метаданными и дельтами метрик.

    Raises:
        ValueError: если runs из разных eval_set_id или меньше 2.
    """
    if len(runs) < 2:
        raise ValueError("Need at least 2 runs to compare")

    # Валидация: все прогоны из одного eval_set
    eval_set_ids = {r.eval_set_id for r in runs}
    if len(eval_set_ids) > 1:
        raise ValueError("Cannot compare runs from different eval sets")

    # Сортировка по ts (по возрастанию) — детерминированный порядок
    sorted_runs = sorted(runs, key=lambda r: r.ts)

    # Извлекаем метаданные
    run_metas = [_run_to_metadata(r) for r in sorted_runs]

    # Вычисляем дельты для последовательных пар
    deltas: list[MetricDelta] = []
    for i in range(len(sorted_runs) - 1):
        earlier = sorted_runs[i]
        later = sorted_runs[i + 1]

        earlier_metrics = earlier.metrics or {}
        later_metrics = later.metrics or {}

        # Собираем все ключи метрик
        all_keys = set(earlier_metrics.keys()) | set(later_metrics.keys())
        for key in sorted(all_keys):
            ev = earlier_metrics.get(key)
            lv = later_metrics.get(key)
            # Пропускаем нечисловые метрики (например, total_items)
            if not isinstance(ev, (int, float)) or not isinstance(lv, (int, float)):
                continue
            delta = round(lv - ev, 4)
            deltas.append(
                MetricDelta(
                    metric_name=key,
                    earlier_value=float(ev),
                    later_value=float(lv),
                    delta=delta,
                    direction=_metric_direction(delta),
                )
            )

    return EvalComparison(
        eval_set_id=sorted_runs[0].eval_set_id,
        runs=run_metas,
        deltas=deltas,
    )
