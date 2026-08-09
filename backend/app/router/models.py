"""Структуры данных для правил маршрутизации. arch.md §7.2, S-12.

Правила — данные, не код. Порядок значим:
первое совпадение задаёт допустимое множество, последующие только сужают.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from app.db.models import Model
from app.policy.models import WILDCARD


@dataclass(frozen=True)
class RouteRule:
    """Одно правило маршрутизации.

    Условия (when_*): все заданные должны совпасть для срабатывания.
    Если все None — правило срабатывает всегда (default).
    Действие: to сужает допустимое множество, fallback задаёт резервные модели.
    terminal=True — прекратить обработку после срабатывания.
    """

    order: int
    is_default: bool = False
    is_terminal: bool = False
    when_corpus_class: list[str] | None = None
    when_role: str | None = None
    when_task: str | None = None
    when_model_alias: str | None = None
    to: list[str] = field(default_factory=list)
    allow_locality: list[str] | None = None
    fallback: list[str] = field(default_factory=list)
    reason: str = ""

    def matches(self, ctx: RouteContext) -> bool:
        """True, если все заданные условия совпадают."""
        if self.is_default:
            return True
        return not (
            (
                self.when_corpus_class is not None
                and (
                    ctx.corpus_data_class is None
                    or ctx.corpus_data_class not in self.when_corpus_class
                )
            )
            or (self.when_role is not None and ctx.user_role_name != self.when_role)
            or (self.when_task is not None and ctx.task_type != self.when_task)
            or (
                self.when_model_alias is not None
                and not fnmatch.fnmatch(ctx.model_alias or "", self.when_model_alias)
            )
        )

    def filter_models(self, candidates: list[Model]) -> list[Model]:
        """Сужает список кандидатов по to и allow_locality."""
        result = candidates
        if self.to:
            result = [m for m in result if _alias_matches(self.to, m.alias)]
        if self.allow_locality is not None:
            result = [m for m in result if m.locality in self.allow_locality]
        return result


def _alias_matches(patterns: list[str], alias: str) -> bool:
    """Проверяет соответствие алиаса шаблонам (fnmatch)."""
    if WILDCARD in patterns:
        return True
    return any(fnmatch.fnmatch(alias, p) for p in patterns)


@dataclass(frozen=True)
class RouteContext:
    """Контекст маршрутизации.

    candidate_models: включённые модели, уже отфильтрованные политикой (policy.models).
    corpus_data_class: None до T-221.
    task_type: None до появления классификации задач.
    """

    candidate_models: list[Model]
    user_role_name: str
    model_alias: str | None = None
    corpus_data_class: str | None = None
    task_type: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    """Решение маршрутизатора.

    rule_index: индекс сработавшего правила (-1 = default).
    fallbacks: резервные модели, уже отфильтрованные по data_class.
    """

    model: Model
    rule_index: int
    fallbacks: list[Model]
    reason: str
