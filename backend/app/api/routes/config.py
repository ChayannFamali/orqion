"""GET /api/config/export, POST /api/config/import (T-425).

Access control: только admin (через "*" в capabilities).
Non-admin → 404 (не раскрываем существование), по прецеденту T-308.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.config import (
    ConfigExportResponse,
    ConfigImportRequest,
    ImportResultResponse,
)
from app.auth.dependencies import current_user
from app.config_io.service import export_config, import_config
from app.db.models import User
from app.db.session import get_session
from app.errors import NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/config",
    tags=["config"],
    dependencies=[Depends(current_user)],
)


async def _check_admin(session: AsyncSession, user: User) -> None:
    """Проверяет admin-доступ. Non-admin → 404."""
    policy = await resolve_policy(session, user)
    if WILDCARD not in policy.capabilities:
        raise NotFound(
            constraint={"object": "config", "reason": "admin required"},
            hint="Нет права на управление конфигурацией",
        )


@router.get("/export", response_model=ConfigExportResponse)
async def export_config_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> ConfigExportResponse:
    await _check_admin(session, user)
    workspace_id = request.app.state.workspace_id
    yaml_content = await export_config(session, workspace_id)
    return ConfigExportResponse(yaml=yaml_content)


@router.post("/import", response_model=ImportResultResponse)
async def import_config_endpoint(
    body: ConfigImportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> ImportResultResponse:
    await _check_admin(session, user)
    workspace_id = request.app.state.workspace_id
    result = await import_config(
        session,
        workspace_id,
        body.yaml,
        dry_run=body.dry_run,
    )
    return ImportResultResponse(
        roles_created=result.roles_created,
        roles_updated=result.roles_updated,
        roles_unchanged=result.roles_unchanged,
        routing_rules_replaced=result.routing_rules_replaced,
        routing_rules_count=result.routing_rules_count,
        warnings=result.warnings,
    )
