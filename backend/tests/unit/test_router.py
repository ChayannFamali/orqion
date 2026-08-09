"""Тесты маршрутизации: упорядоченное сужение, fallback, data_class, no route.

S-12: первое совпадение задаёт множество, последующие только сужают.
fallback применяется при недоступности провайдера, не при отказе по правам.
Фильтр по классу данных применяется до fallback и к нему тоже.
"""

from __future__ import annotations

import pytest
from app.db.models import Model
from app.errors import NoRouteAvailable
from app.router.models import RouteContext, RouteRule
from app.router.service import select_model


def _make_model(
    alias: str,
    locality: str = "local",
    provider_id: str = "prov-1",
    enabled: bool = True,
) -> Model:
    return Model(
        workspace_id="ws-1",
        provider_id=provider_id,
        alias=alias,
        upstream_name=alias,
        locality=locality,
        max_input_tokens=None,
        max_output_tokens=None,
        supports_reasoning=False,
        cost_in=None,
        cost_out=None,
        enabled=enabled,
    )


def _make_ctx(
    candidates: list[Model],
    role: str = "developer",
    model_alias: str | None = None,
    corpus_data_class: str | None = None,
    task_type: str | None = None,
) -> RouteContext:
    return RouteContext(
        candidate_models=candidates,
        user_role_name=role,
        model_alias=model_alias,
        corpus_data_class=corpus_data_class,
        task_type=task_type,
    )


class TestSelectDefault:
    """Default-правило: все кандидаты проходят."""

    def test_default_returns_first_candidate(self) -> None:
        models = [_make_model("local/qwen3-8b"), _make_model("local/qwen3-14b")]
        rules = [
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        ctx = _make_ctx(models)
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"
        assert decision.rule_index == 99
        assert decision.reason == "default"

    def test_default_no_fallback_when_not_specified(self) -> None:
        models = [_make_model("local/qwen3-8b")]
        rules = [
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        ctx = _make_ctx(models)
        decision = select_model(rules, ctx)
        assert decision.fallbacks == []


class TestOrderedNarrowing:
    """Упорядоченное сужение: каждое совпадение уменьшает множество."""

    def test_first_match_narrows_then_default_keeps(self) -> None:
        """Правило сужает до local/*, default не расширяет обратно.

        reason — от последнего сработавшего правила (default),
        но множество не расширяется обратно: external/claude-sonnet исключён.
        """
        models = [
            _make_model("local/qwen3-8b"),
            _make_model("external/claude-sonnet", locality="external"),
        ]
        rules = [
            RouteRule(
                order=0,
                is_default=False,
                is_terminal=False,
                when_role="developer",
                to=["local/*"],
                reason="developer → local first",
            ),
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        ctx = _make_ctx(models, role="developer")
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"
        assert decision.reason == "default"

    def test_terminal_stops_processing(self) -> None:
        """terminal=True — последующие правила не выполняются."""
        models = [
            _make_model("local/qwen3-8b"),
            _make_model("local/qwen3-14b"),
        ]
        rules = [
            RouteRule(
                order=0,
                is_default=False,
                is_terminal=True,
                when_role="support",
                to=["local/qwen3-8b"],
                reason="support → qwen3-8b only",
            ),
            RouteRule(
                order=1,
                is_default=False,
                is_terminal=False,
                to=["local/qwen3-14b"],
                reason="should-not-reach",
            ),
        ]
        ctx = _make_ctx(models, role="support")
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"
        assert decision.reason == "support → qwen3-8b only"

    def test_non_terminal_continues_narrowing(self) -> None:
        """is_terminal=False — следующее правило может сузить дальше."""
        models = [
            _make_model("local/qwen3-8b"),
            _make_model("local/qwen3-14b"),
            _make_model("local/qwen3-32b"),
        ]
        rules = [
            RouteRule(
                order=0,
                is_terminal=False,
                when_role="developer",
                to=["local/*"],
                reason="local only",
            ),
            RouteRule(
                order=1,
                is_terminal=True,
                when_role="developer",
                to=["local/qwen3-8b"],
                reason="narrowed to 8b",
            ),
        ]
        ctx = _make_ctx(models, role="developer")
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"
        assert decision.reason == "narrowed to 8b"

    def test_rule_with_no_match_skipped(self) -> None:
        """Правило для role=support не срабатывает для developer."""
        models = [
            _make_model("local/qwen3-8b"),
            _make_model("external/gpt-4", locality="external"),
        ]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                when_role="support",
                to=["local/qwen3-8b"],
                reason="support-only",
            ),
            RouteRule(
                order=99,
                is_default=True,
                is_terminal=True,
                reason="default",
            ),
        ]
        ctx = _make_ctx(models, role="developer")
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"
        assert decision.reason == "default"


class TestDataClassFilter:
    """К2/К3 → только local, применяется до fallback и к нему тоже."""

    def test_k3_excludes_external_models(self) -> None:
        models = [
            _make_model("local/qwen3-8b"),
            _make_model("external/gpt-4", locality="external"),
        ]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                when_corpus_class=["К3"],
                allow_locality=["local"],
                reason="конфиденциальный корпус",
            ),
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        ctx = _make_ctx(models, corpus_data_class="К3")
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"
        assert decision.model.locality == "local"

    def test_k2_excludes_external_even_without_explicit_rule(self) -> None:
        """data_class фильтр применяется к кандидатам до правил."""
        models = [
            _make_model("local/qwen3-8b"),
            _make_model("external/gpt-4", locality="external"),
        ]
        rules = [
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        ctx = _make_ctx(models, corpus_data_class="К2")
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"

    def test_k0_allows_external(self) -> None:
        models = [
            _make_model("local/qwen3-8b"),
            _make_model("external/gpt-4", locality="external"),
        ]
        rules = [
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        ctx = _make_ctx(models, corpus_data_class="К0")
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"

    def test_fallback_filtered_by_data_class(self) -> None:
        """Fallback не содержит внешние модели для К3."""
        models = [
            _make_model("local/qwen3-8b"),
            _make_model("local/qwen3-4b"),
            _make_model("external/gpt-4", locality="external"),
        ]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                to=["local/qwen3-8b"],
                fallback=["local/qwen3-4b", "external/gpt-4"],
                reason="primary+fallback",
            ),
        ]
        ctx = _make_ctx(models, corpus_data_class="К3")
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"
        fallback_aliases = [m.alias for m in decision.fallbacks]
        assert "local/qwen3-4b" in fallback_aliases
        assert "external/gpt-4" not in fallback_aliases


class TestFallback:
    """Fallback: список резервных моделей, основная исключена."""

    def test_fallback_returned_without_primary(self) -> None:
        models = [
            _make_model("local/qwen3-8b"),
            _make_model("local/qwen3-4b"),
        ]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                to=["local/qwen3-8b"],
                fallback=["local/qwen3-4b"],
                reason="primary+fallback",
            ),
        ]
        ctx = _make_ctx(models)
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"
        assert len(decision.fallbacks) == 1
        assert decision.fallbacks[0].alias == "local/qwen3-4b"

    def test_primary_not_in_fallback(self) -> None:
        models = [_make_model("local/qwen3-8b")]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                to=["local/qwen3-8b"],
                fallback=["local/qwen3-8b"],
                reason="dedup",
            ),
        ]
        ctx = _make_ctx(models)
        decision = select_model(rules, ctx)
        assert decision.model.alias == "local/qwen3-8b"
        assert decision.fallbacks == []

    def test_fallback_missing_alias_skipped(self) -> None:
        """Алиас fallback, не существующий в кандидатах, просто пропускается."""
        models = [_make_model("local/qwen3-8b")]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                to=["local/qwen3-8b"],
                fallback=["local/nonexistent"],
                reason="missing-fallback",
            ),
        ]
        ctx = _make_ctx(models)
        decision = select_model(rules, ctx)
        assert decision.fallbacks == []


class TestNoRoute:
    """Нет маршрута → NoRouteAvailable."""

    def test_no_candidates_raises(self) -> None:
        rules = [
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        ctx = _make_ctx([])
        with pytest.raises(NoRouteAvailable) as exc_info:
            select_model(rules, ctx)
        assert exc_info.value.constraint is not None
        assert exc_info.value.constraint["reason"] == "default"

    def test_rule_narrows_to_empty_raises(self) -> None:
        """Правило сужает до несуществующего алиаса → пусто → отказ."""
        models = [_make_model("local/qwen3-8b")]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                when_role="support",
                to=["local/nonexistent"],
                reason="narrowed-to-empty",
            ),
        ]
        ctx = _make_ctx(models, role="support")
        with pytest.raises(NoRouteAvailable) as exc_info:
            select_model(rules, ctx)
        assert exc_info.value.constraint is not None
        assert exc_info.value.constraint["reason"] == "narrowed-to-empty"

    def test_k3_only_external_models_raises(self) -> None:
        """К3 + только внешние модели → data_class фильтр → пусто → отказ."""
        models = [_make_model("external/gpt-4", locality="external")]
        rules = [
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        ctx = _make_ctx(models, corpus_data_class="К3")
        with pytest.raises(NoRouteAvailable):
            select_model(rules, ctx)


class TestRuleMatching:
    """Условия срабатывания правил."""

    def test_when_model_alias_glob(self) -> None:
        """when_model_alias с шаблоном fnmatch."""
        models = [_make_model("local/qwen3-8b")]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                when_model_alias="local/qwen3-*",
                to=["local/qwen3-8b"],
                reason="glob-match",
            ),
        ]
        ctx = _make_ctx(models, model_alias="local/qwen3-8b")
        decision = select_model(rules, ctx)
        assert decision.reason == "glob-match"

    def test_when_model_alias_no_match(self) -> None:
        """when_model_alias не совпадает → правило пропускается."""
        models = [_make_model("local/qwen3-8b")]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                when_model_alias="external/*",
                to=["local/qwen3-8b"],
                reason="should-not-match",
            ),
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        ctx = _make_ctx(models, model_alias="local/qwen3-8b")
        decision = select_model(rules, ctx)
        assert decision.reason == "default"

    def test_when_task_matches(self) -> None:
        models = [_make_model("local/qwen3-8b")]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                when_task="code",
                to=["local/qwen3-8b"],
                reason="code-task",
            ),
        ]
        ctx = _make_ctx(models, task_type="code")
        decision = select_model(rules, ctx)
        assert decision.reason == "code-task"

    def test_multiple_conditions_all_must_match(self) -> None:
        """Все заданные when_* должны совпасть одновременно."""
        models = [_make_model("local/qwen3-8b")]
        rules = [
            RouteRule(
                order=0,
                is_terminal=True,
                when_role="developer",
                when_task="code",
                to=["local/qwen3-8b"],
                reason="developer+code",
            ),
            RouteRule(order=99, is_default=True, is_terminal=True, reason="default"),
        ]
        # role совпадает, task нет → default
        ctx1 = _make_ctx(models, role="developer", task_type="chat")
        decision1 = select_model(rules, ctx1)
        assert decision1.reason == "default"

        # оба совпадают
        ctx2 = _make_ctx(models, role="developer", task_type="code")
        decision2 = select_model(rules, ctx2)
        assert decision2.reason == "developer+code"
