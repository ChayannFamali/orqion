"""GET /api/audit-log — read API для журнала аудита (T-317).

Access control: только admin (через "*" в capabilities).
Non-admin → 404 (прецедент T-308/T-310).
workspace_id: request.app.state.workspace_id (ADR-3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.audit import (
    AuditActionsResponse,
    AuditLogListResponse,
    AuditLogResponse,
)
from app.audit.service import list_audit, list_distinct_actions
from app.auth.dependencies import current_user
from app.db.models import User
from app.db.session import get_session
from app.errors import NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/audit-log",
    tags=["audit"],
    dependencies=[Depends(current_user)],
)


async def _check_admin(session: AsyncSession, user: User) -> bool:
    """True если admin (через *). Иначе — NotFound (не раскрываем существование)."""
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities


@router.get("", response_model=AuditLogListResponse)
async def list_audit_entries(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None),
    actor_user_id: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> AuditLogListResponse:
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "audit_log", "reason": "admin required"},
            hint="Нет права на просмотр журнала аудита",
        )

    workspace_id = request.app.state.workspace_id
    entries, total = await list_audit(
        session,
        workspace_id,
        limit=limit,
        offset=offset,
        action=action,
        actor_user_id=actor_user_id,
        start_date=start,
        end_date=end,
    )
    return AuditLogListResponse(
        entries=[
            AuditLogResponse(
                id=e.id,
                ts=e.ts,
                actor_user_id=e.actor_user_id,
                action=e.action,
                object_type=e.object_type,
                object_id=e.object_id,
                meta=e.meta,
            )
            for e in entries
        ],
        total=total,
    )


@router.get("/actions", response_model=AuditActionsResponse)
async def get_audit_actions(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> AuditActionsResponse:
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "audit_log", "reason": "admin required"},
            hint="Нет права на просмотр журнала аудита",
        )

    workspace_id = request.app.state.workspace_id
    actions = await list_distinct_actions(session, workspace_id)
    return AuditActionsResponse(actions=actions)
