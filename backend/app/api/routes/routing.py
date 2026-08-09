"""CRUD правил маршрутизации (T-114a).

Доступ — через capability manage_routing, не через role.name (§5.2).
Инвариант ADR-12: _filter_data_class вызывается безусловно в коде,
удаление seed-правила К2/К3 не влияет на фильтр.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.routing import (
    RoutingRuleCreate,
    RoutingRuleListResponse,
    RoutingRuleResponse,
    RoutingRuleUpdate,
)
from app.auth.dependencies import current_user
from app.db.models import RoutingRule, User
from app.db.session import get_session
from app.errors import OrqionError
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/routing-rules",
    tags=["routing-rules"],
    dependencies=[Depends(current_user)],
)


class RoutingPermissionDenied(OrqionError):
    error_code = "routing_permission_denied"
    reason = "Нет прав для управления правилами маршрутизации"
    status_code = 403
    hint = "Требуется capability manage_routing"


class DuplicateRuleOrder(OrqionError):
    error_code = "duplicate_rule_order"
    reason = "Правило с таким order уже существует"
    status_code = 409


class RuleNotFound(OrqionError):
    error_code = "rule_not_found"
    reason = "Правило не найдено"
    status_code = 404


async def _check_manage_routing(
    session: AsyncSession,
    user: User,
) -> None:
    """Проверяет capability manage_routing через resolve_policy."""
    policy = await resolve_policy(session, user)
    if WILDCARD not in policy.capabilities and "manage_routing" not in policy.capabilities:
        raise RoutingPermissionDenied()


def _to_response(rule: RoutingRule) -> RoutingRuleResponse:
    return RoutingRuleResponse(
        id=rule.id,
        order=rule.order,
        is_default=rule.is_default,
        is_terminal=rule.is_terminal,
        when_corpus_class=rule.when_corpus_class,
        when_role=rule.when_role,
        when_task=rule.when_task,
        when_model_alias=rule.when_model_alias,
        to_models=rule.to_models,
        allow_locality=rule.allow_locality,
        fallback_models=rule.fallback_models,
        reason=rule.reason,
    )


@router.get("", response_model=RoutingRuleListResponse)
async def list_routing_rules(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> RoutingRuleListResponse:
    """Список правил маршрутизации. Доступ: manage_routing."""
    await _check_manage_routing(session, user)
    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(RoutingRule)
        .where(RoutingRule.workspace_id == workspace_id)
        .order_by(RoutingRule.order)
    )
    rules = result.scalars().all()
    return RoutingRuleListResponse(
        rules=[_to_response(r) for r in rules],
        total=len(rules),
    )


@router.post("", response_model=RoutingRuleResponse, status_code=201)
async def create_routing_rule(
    body: RoutingRuleCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> RoutingRuleResponse:
    """Создание правила маршрутизации. Доступ: manage_routing."""
    await _check_manage_routing(session, user)
    workspace_id = request.app.state.workspace_id

    # Проверка дубликата order
    existing = await session.execute(
        select(RoutingRule).where(
            RoutingRule.workspace_id == workspace_id,
            RoutingRule.order == body.order,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateRuleOrder(
            constraint={"order": body.order},
            hint="Используйте другой order или измените существующее правило",
        )

    rule = RoutingRule(
        workspace_id=workspace_id,
        order=body.order,
        is_default=body.is_default,
        is_terminal=body.is_terminal,
        when_corpus_class=body.when_corpus_class,
        when_role=body.when_role,
        when_task=body.when_task,
        when_model_alias=body.when_model_alias,
        to_models=body.to_models,
        allow_locality=body.allow_locality,
        fallback_models=body.fallback_models,
        reason=body.reason,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _to_response(rule)


@router.patch("/{rule_id}", response_model=RoutingRuleResponse)
async def update_routing_rule(
    rule_id: str,
    body: RoutingRuleUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> RoutingRuleResponse:
    """Обновление правила маршрутизации. Доступ: manage_routing."""
    await _check_manage_routing(session, user)
    workspace_id = request.app.state.workspace_id

    rule = await session.get(RoutingRule, rule_id)
    if rule is None or rule.workspace_id != workspace_id:
        raise RuleNotFound(
            constraint={"id": rule_id},
            hint="Правило не найдено в этом workspace",
        )

    # Проверка дубликата order при изменении
    if body.order is not None and body.order != rule.order:
        existing = await session.execute(
            select(RoutingRule).where(
                RoutingRule.workspace_id == workspace_id,
                RoutingRule.order == body.order,
                RoutingRule.id != rule_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateRuleOrder(
                constraint={"order": body.order},
                hint="Используйте другой order или измените существующее правило",
            )

    updates = body.model_dump(exclude_unset=True)
    for field_name, value in updates.items():
        setattr(rule, field_name, value)

    await session.commit()
    await session.refresh(rule)
    return _to_response(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_routing_rule(
    rule_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> None:
    """Удаление правила маршрутизации. Доступ: manage_routing.

    ADR-12: удаление seed-правила К2/К3 не влияет на фильтр data_class,
    т.к. _filter_data_class вызывается безусловно в коде.
    """
    await _check_manage_routing(session, user)
    workspace_id = request.app.state.workspace_id

    rule = await session.get(RoutingRule, rule_id)
    if rule is None or rule.workspace_id != workspace_id:
        raise RuleNotFound(
            constraint={"id": rule_id},
            hint="Правило не найдено в этом workspace",
        )

    await session.delete(rule)
    await session.commit()
