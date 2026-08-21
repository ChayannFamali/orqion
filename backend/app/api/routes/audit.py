"""GET /api/audit-log — read API для журнала аудита (T-317).
GET /api/audit-log/export — экспорт в CSV/JSON (T-428).

Access control: только admin (через "*" в capabilities).
Non-admin → 404 (прецедент T-308/T-310).
workspace_id: request.app.state.workspace_id (ADR-3).
"""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.audit import (
    AuditActionsResponse,
    AuditLogListResponse,
    AuditLogResponse,
)
from app.audit.service import list_audit, list_audit_export, list_distinct_actions
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


@router.get("/export")
async def export_audit_log(
    request: Request,
    format: str = Query("json", pattern="^(csv|json)$"),
    action: str | None = Query(None),
    actor_user_id: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(10_000, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Экспорт журнала аудита в CSV или JSON (T-428).

    Сортировка: ts ASC, id ASC (хронологическая, стабильная для offset-пагинации).
    Лимит: до 10 000 строк за запрос. Headers: X-Export-Total, X-Export-Count.
    """
    if not await _check_admin(session, user):
        raise NotFound(
            constraint={"object": "audit_log", "reason": "admin required"},
            hint="Нет права на экспорт журнала аудита",
        )

    workspace_id = request.app.state.workspace_id
    entries, total = await list_audit_export(
        session,
        workspace_id,
        limit=limit,
        offset=offset,
        action=action,
        actor_user_id=actor_user_id,
        start_date=start,
        end_date=end,
    )

    count = len(entries)
    headers = {
        "X-Export-Total": str(total),
        "X-Export-Count": str(count),
        "Content-Disposition": f"attachment; filename=audit-log.{format}",
    }

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ts", "actor_user_id", "action", "object_type", "object_id", "meta"])
        for e in entries:
            writer.writerow(
                [
                    e.ts.isoformat(),
                    e.actor_user_id,
                    e.action,
                    e.object_type,
                    e.object_id or "",
                    json.dumps(e.meta, ensure_ascii=False),
                ]
            )
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers=headers,
        )

    data = [
        {
            "ts": e.ts.isoformat(),
            "actor_user_id": e.actor_user_id,
            "action": e.action,
            "object_type": e.object_type,
            "object_id": e.object_id,
            "meta": e.meta,
        }
        for e in entries
    ]
    content = json.dumps(
        {"entries": data, "total": total, "exported": count},
        ensure_ascii=False,
        indent=2,
    )
    return Response(
        content=content,
        media_type="application/json",
        headers=headers,
    )
