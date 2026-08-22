"""GET /api/models — список моделей, доступных текущему пользователю.

Фильтрует по policy.models (resolve_policy) и enabled=True.
Возвращает плоский список моделей, не сгруппированный по провайдерам.
"""

from __future__ import annotations

import fnmatch

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.provider import ModelResponse
from app.auth.dependencies import current_user
from app.db.models import Model, Provider, User
from app.db.session import get_session
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(prefix="/api/models", tags=["models"], dependencies=[Depends(current_user)])


def _filter_by_policy(
    models: list[Model],
    policy_models: list[str],
) -> list[Model]:
    """Фильтрует модели по policy.models.

    policy.models=["*"] или пустой — все модели.
    Иначе — fnmatch по алиасам.
    """
    if not policy_models or WILDCARD in policy_models:
        return models
    return [m for m in models if any(fnmatch.fnmatch(m.alias, p) for p in policy_models)]


@router.get("", response_model=list[ModelResponse])
async def list_available_models(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> list[ModelResponse]:
    """Возвращает модели, доступные текущему пользователю.

    Фильтрация:
    1. enabled=True у модели и провайдера
    2. policy.models для роли пользователя (resolve_policy)
    """
    workspace_id = request.app.state.workspace_id

    # ORDER BY alias — тот же порядок, что у кандидатов маршрутизации:
    # первый элемент списка = неявный дефолт UI совпадает с candidates[0]
    result = await session.execute(
        select(Model, Provider.kind)
        .join(Provider, Model.provider_id == Provider.id)
        .where(
            Model.workspace_id == workspace_id,
            Model.enabled.is_(True),
            Provider.enabled.is_(True),
        )
        .order_by(Model.alias)
    )
    all_models = [(m, kind) for m, kind in result.all()]

    policy = await resolve_policy(session, user)
    filtered = _filter_by_policy([m for m, _ in all_models], policy.models)
    kind_by_id = {m.id: kind for m, kind in all_models}

    return [
        ModelResponse(
            id=m.id,
            alias=m.alias,
            upstream_name=m.upstream_name,
            locality=m.locality,
            provider_kind=kind_by_id[m.id],
            max_input_tokens=m.max_input_tokens,
            max_output_tokens=m.max_output_tokens,
            supports_reasoning=m.supports_reasoning,
            cost_in=m.cost_in,
            cost_out=m.cost_out,
            enabled=m.enabled,
        )
        for m in filtered
    ]
