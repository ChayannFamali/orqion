"""select(): упорядоченное сужение, fallback, data_class-фильтр.

arch.md §7.2: первое совпадение задаёт множество, последующие только сужают.
fallback применяется при недоступности провайдера, не при отказе по правам.
Фильтр по классу данных применяется до fallback и к нему тоже (S-12, грабли).
BUG-012: явный выбор пользователя становится primary внутри уже суженного
множества — выбор переставляет приоритет, не расширяя множество.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Model, Provider, RoutingRule
from app.errors import NoRouteAvailable
from app.router.models import RouteContext, RouteDecision, RouteRule


async def load_rules(session: AsyncSession, workspace_id: str) -> list[RouteRule]:
    """Загружает правила из БД, отсортированные по order."""
    result = await session.execute(
        select(RoutingRule)
        .where(RoutingRule.workspace_id == workspace_id)
        .order_by(RoutingRule.order)
    )
    rows = result.scalars().all()
    return [
        RouteRule(
            order=r.order,
            is_default=r.is_default,
            is_terminal=r.is_terminal,
            when_corpus_class=r.when_corpus_class,
            when_role=r.when_role,
            when_task=r.when_task,
            when_model_alias=r.when_model_alias,
            to=r.to_models or [],
            allow_locality=r.allow_locality,
            fallback=r.fallback_models or [],
            reason=r.reason or "",
        )
        for r in rows
    ]


def _filter_data_class(
    models: list[Model],
    corpus_data_class: str | None,
) -> list[Model]:
    """Убирает внешние модели для К2/К3 (ADR-12). Применяется к fallback тоже."""
    if corpus_data_class in ("К2", "К3"):
        return [m for m in models if m.locality == "local"]
    return models


def select_model(
    rules: list[RouteRule],
    ctx: RouteContext,
) -> RouteDecision:
    """Выбирает модель по правилам. Возбуждает NoRouteAvailable при пустом множестве.

    Алгоритм:
    1. Начать с кандидатов, отфильтрованных по data_class.
    2. Проходить правила по порядку. Каждое совпадение сужает множество.
    3. terminal=True — прекратить после срабатывания.
    4. Если ни одно правило не сработало — использовать исходное множество.
    5. BUG-012: model_alias из контекста становится основным, если присутствует
       в итоговом множестве (проверка после всех сужений — выбор не обходит
       ADR-12 и policy-видимость), reason — "user selection (alias)".
       Иначе — первая модель множества.
    6. Fallback-цепочка остаётся заданной правилами.
    """
    candidates = _filter_data_class(ctx.candidate_models, ctx.corpus_data_class)

    matched_rule_index = -1
    matched_reason = "default"
    matched_fallback: list[str] = []

    for rule in rules:
        if not rule.matches(ctx):
            continue
        matched_rule_index = rule.order
        matched_reason = rule.reason or f"rule-{rule.order}"
        matched_fallback = rule.fallback
        candidates = rule.filter_models(candidates)
        if rule.is_terminal:
            break

    if not candidates:
        constraint: dict[str, object] = {
            "reason": matched_reason,
            "rule_index": matched_rule_index,
            "candidate_count": len(ctx.candidate_models),
        }
        hint = "Нет моделей, удовлетворяющих правилам маршрутизации и ограничениям"
        if ctx.corpus_data_class in ("К2", "К3"):
            constraint["data_class"] = ctx.corpus_data_class
            constraint["filtered_locality"] = "local"
            hint = (
                f"Корпус класса {ctx.corpus_data_class} допускает только локальные модели. "
                "Все локальные модели отфильтрованы правилами маршрутизации или недоступны"
            )
        raise NoRouteAvailable(
            constraint=constraint,
            hint=hint,
        )

    # BUG-012: явный выбор пользователя становится primary, если модель прошла
    # все сужения (policy-видимость и data_class-фильтр применены выше) —
    # выбор переставляет приоритет внутри разрешённого множества, не расширяя его.
    chosen: Model | None = None
    if ctx.model_alias is not None:
        chosen = next((m for m in candidates if m.alias == ctx.model_alias), None)
    if chosen is not None:
        primary = chosen
        matched_reason = f"user selection ({ctx.model_alias})"
    else:
        primary = candidates[0]
    fallback_models = _filter_data_class(
        _select_by_aliases(ctx.candidate_models, matched_fallback),
        ctx.corpus_data_class,
    )
    # Основная модель не повторяется в fallback (сравнение по alias — работает и без id)
    fallback_models = [m for m in fallback_models if m.alias != primary.alias]

    return RouteDecision(
        model=primary,
        rule_index=matched_rule_index,
        fallbacks=fallback_models,
        reason=matched_reason,
    )


def _select_by_aliases(models: list[Model], aliases: list[str]) -> list[Model]:
    """Выбирает модели по списку алиасов, сохраняя порядок алиасов."""
    if not aliases:
        return []
    by_alias = {m.alias: m for m in models}
    return [by_alias[a] for a in aliases if a in by_alias]


async def load_candidate_models(
    session: AsyncSession,
    workspace_id: str,
) -> list[Model]:
    """Загружает все включённые модели с включёнными провайдерами.

    ORDER BY alias: порядок кандидатов детерминирован (алиас уникален в
    workspace) — дефолтный primary не зависит от БД и плана запроса.
    """
    result = await session.execute(
        select(Model)
        .join(Provider, Model.provider_id == Provider.id)
        .where(
            Model.workspace_id == workspace_id,
            Model.enabled.is_(True),
            Provider.enabled.is_(True),
        )
        .order_by(Model.alias)
    )
    return list(result.scalars().all())
