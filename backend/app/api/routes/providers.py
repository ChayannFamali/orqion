"""POST /api/providers, GET /api/providers, PATCH /api/providers/{id}.

Ключ шифруется при записи, не возвращается в ответах (AGENTS.md §14).
Access control: capability "manage_providers" (только admin через "*" в seed presets).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.provider import (
    ModelCreate,
    ModelResponse,
    ModelUpdate,
    ProviderCreate,
    ProviderListResponse,
    ProviderResponse,
    ProviderUpdate,
)
from app.auth.dependencies import current_user
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, User
from app.db.session import get_session
from app.errors import BadRequest, NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/providers", tags=["providers"], dependencies=[Depends(current_user)]
)


async def _check_manage_providers(session: AsyncSession, user: User) -> bool:
    """True если admin (через *). Иначе — NotFound (не раскрываем существование)."""
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities or "manage_providers" in policy.capabilities


def _provider_to_response(provider: Provider) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        kind=provider.kind,
        base_url=provider.base_url,
        enabled=provider.enabled,
        capabilities=provider.capabilities,
        models=[_model_to_response(m) for m in sorted(provider.models, key=lambda m: m.alias)],
    )


def _model_to_response(model: Model) -> ModelResponse:

    return ModelResponse(
        id=model.id,
        alias=model.alias,
        upstream_name=model.upstream_name,
        locality=model.locality,
        max_input_tokens=model.max_input_tokens,
        max_output_tokens=model.max_output_tokens,
        supports_reasoning=model.supports_reasoning,
        cost_in=model.cost_in,
        cost_out=model.cost_out,
        enabled=model.enabled,
    )


@router.post("", response_model=ProviderResponse, status_code=201)
async def create_provider(
    body: ProviderCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> ProviderResponse:
    if not await _check_manage_providers(session, user):
        raise NotFound(
            constraint={"object": "providers", "reason": "manage_providers required"},
            hint="Нет права на управление провайдерами",
        )

    secret_key = request.app.state.secret_key

    api_key_enc = None
    if body.api_key:
        api_key_enc = encrypt_api_key(body.api_key, secret_key)

    provider = Provider(
        workspace_id=request.app.state.workspace_id,
        kind=body.kind,
        base_url=body.base_url,
        api_key_enc=api_key_enc,
        enabled=body.enabled,
        capabilities={},
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider, ["models"])

    return _provider_to_response(provider)


@router.get("", response_model=ProviderListResponse)
async def list_providers(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> ProviderListResponse:
    if not await _check_manage_providers(session, user):
        raise NotFound(
            constraint={"object": "providers", "reason": "manage_providers required"},
            hint="Нет права на управление провайдерами",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Provider)
        .where(Provider.workspace_id == workspace_id)
        .options(selectinload(Provider.models))
    )
    providers = result.scalars().unique().all()
    return ProviderListResponse(
        providers=[_provider_to_response(p) for p in providers],
    )


@router.patch("/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: str,
    body: ProviderUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> ProviderResponse:
    if not await _check_manage_providers(session, user):
        raise NotFound(
            constraint={"object": "providers", "reason": "manage_providers required"},
            hint="Нет права на управление провайдерами",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Provider)
        .where(Provider.id == provider_id, Provider.workspace_id == workspace_id)
        .options(selectinload(Provider.models))
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise NotFound(
            constraint={"object": "provider", "id": provider_id},
            hint="Провайдер не найден",
        )

    if body.base_url is not None:
        provider.base_url = body.base_url
    if body.api_key is not None:
        secret_key = request.app.state.secret_key
        provider.api_key_enc = encrypt_api_key(body.api_key, secret_key)
    if body.enabled is not None:
        provider.enabled = body.enabled

    await session.commit()
    await session.refresh(provider, ["models"])

    return _provider_to_response(provider)


@router.post("/{provider_id}/probe")
async def probe_provider_endpoint(
    provider_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
    deep: bool = False,
) -> dict[str, object]:
    """Запускает probe провайдера: измеряет возможности, сверяет модели.

    При deep=true — дополнительно измеряет фактический контекст (observed_context)
    для каждой available модели. Бинарный поиск, максимум 4 попытки на модель.
    Дорого: отправляет реальные промпты. Не входит в плановый ре-probe (T-112a).
    """
    if not await _check_manage_providers(session, user):
        raise NotFound(
            constraint={"object": "providers", "reason": "manage_providers required"},
            hint="Нет права на управление провайдерами",
        )

    from app.providers.probe import probe_provider

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Provider)
        .where(Provider.id == provider_id, Provider.workspace_id == workspace_id)
        .options(selectinload(Provider.models))
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise NotFound(
            constraint={"object": "provider", "id": provider_id},
            hint="Провайдер не найден",
        )

    secret_key = request.app.state.secret_key
    probe_result = await probe_provider(provider, list(provider.models), secret_key)

    provider.capabilities = {
        "available_models": probe_result.available_models,
        "supports_streaming": probe_result.supports_streaming,
        "max_parallel": probe_result.max_parallel,
        "last_probe_at": probe_result.probed_at.isoformat(),
    }
    provider.last_probe_at = probe_result.probed_at

    response: dict[str, object] = {
        "available_models": probe_result.available_models,
        "supports_streaming": probe_result.supports_streaming,
        "max_parallel": probe_result.max_parallel,
        "model_statuses": [s.model_dump() for s in probe_result.model_statuses],
        "error": probe_result.error,
    }

    if deep and probe_result.error is None:
        from app.providers.deep_probe import measure_observed_context

        observed: dict[str, int | None] = {}
        for ms in probe_result.model_statuses:
            if ms.status == "available":
                model_obj = next(
                    (m for m in provider.models if m.id == ms.model_id),
                    None,
                )
                if model_obj is not None:
                    ctx = await measure_observed_context(provider, model_obj, secret_key)
                    observed[ms.alias] = ctx

        response["observed_context"] = observed

    await session.commit()

    return response


@router.post("/{provider_id}/models", response_model=ModelResponse, status_code=201)
async def create_model(
    provider_id: str,
    body: ModelCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> ModelResponse:
    if not await _check_manage_providers(session, user):
        raise NotFound(
            constraint={"object": "providers", "reason": "manage_providers required"},
            hint="Нет права на управление провайдерами",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Provider).where(Provider.id == provider_id, Provider.workspace_id == workspace_id)
    )
    if result.scalar_one_or_none() is None:
        raise NotFound(
            constraint={"object": "provider", "id": provider_id},
            hint="Провайдер не найден",
        )

    model = Model(
        workspace_id=workspace_id,
        provider_id=provider_id,
        alias=body.alias,
        upstream_name=body.upstream_name,
        locality=body.locality,
        max_input_tokens=body.max_input_tokens,
        max_output_tokens=body.max_output_tokens,
        supports_reasoning=body.supports_reasoning,
        cost_in=body.cost_in,
        cost_out=body.cost_out,
        enabled=body.enabled,
    )
    session.add(model)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise BadRequest(
            "Алиас модели должен быть уникален в рамках workspace",
            hint=f"Алиас '{body.alias}' уже существует",
        )
    await session.refresh(model)

    return _model_to_response(model)


@router.patch("/models/{model_id}", response_model=ModelResponse)
async def update_model(
    model_id: str,
    body: ModelUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> ModelResponse:
    if not await _check_manage_providers(session, user):
        raise NotFound(
            constraint={"object": "providers", "reason": "manage_providers required"},
            hint="Нет права на управление провайдерами",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(Model).where(Model.id == model_id, Model.workspace_id == workspace_id)
    )
    model = result.scalar_one_or_none()
    if model is None:
        raise NotFound(
            constraint={"object": "model", "id": model_id},
            hint="Модель не найдена",
        )

    for field in (
        "alias",
        "upstream_name",
        "locality",
        "max_input_tokens",
        "max_output_tokens",
        "supports_reasoning",
        "cost_in",
        "cost_out",
        "enabled",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(model, field, value)

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise BadRequest(
            "Алиас модели должен быть уникален в рамках workspace",
            hint=f"Алиас '{body.alias}' уже существует",
        )
    await session.refresh(model)

    return _model_to_response(model)
