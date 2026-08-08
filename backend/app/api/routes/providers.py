"""POST /api/providers, GET /api/providers, PATCH /api/providers/{id}.

Ключ шифруется при записи, не возвращается в ответах (AGENTS.md §14).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.provider import (
    ModelResponse,
    ProviderCreate,
    ProviderListResponse,
    ProviderResponse,
    ProviderUpdate,
)
from app.auth.dependencies import current_user
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider
from app.db.session import get_session
from app.errors import NotFound

router = APIRouter(
    prefix="/api/providers", tags=["providers"], dependencies=[Depends(current_user)]
)


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
) -> ProviderResponse:
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
) -> ProviderListResponse:
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
) -> ProviderResponse:
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
