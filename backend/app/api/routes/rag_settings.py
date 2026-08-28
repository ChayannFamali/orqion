"""GET /api/rag-settings, PUT /api/rag-settings.

Настройки RAG-поиска уровня рабочей области (Т-506): порог релевантности
после реранкинга и максимум фрагментов контекста. Одна запись на рабочую
область; до первого изменения действуют значения по умолчанию (8 и 0),
поведение поиска не меняется.

Доступ: чтение — всем авторизованным; изменение — право ``manage_corpora``
(паттерн других разделов управления корпусами: без права → 404).
Изменение пишется в журнал аудита одним действием ``rag_settings.changed``
со старым и новым значением обоих полей; сохранение без изменений запись
не создаёт.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.rag_settings import RagSettingsResponse, RagSettingsUpdate
from app.audit.service import write_audit
from app.auth.dependencies import current_user
from app.db.models import RagSettings, User
from app.db.session import get_session
from app.errors import NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/rag-settings",
    tags=["rag-settings"],
    dependencies=[Depends(current_user)],
)

DEFAULT_RELEVANCE_THRESHOLD = 0
DEFAULT_MAX_FRAGMENTS = 8


async def _check_manage_corpora(session: AsyncSession, user: User) -> bool:
    """True если manage_corpora или * в capabilities."""
    policy = await resolve_policy(session, user)
    if WILDCARD in policy.capabilities:
        return True
    return "manage_corpora" in policy.capabilities


@router.get("", response_model=RagSettingsResponse)
async def get_rag_settings(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RagSettingsResponse:
    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(RagSettings).where(RagSettings.workspace_id == workspace_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return RagSettingsResponse(
            relevance_threshold=DEFAULT_RELEVANCE_THRESHOLD,
            max_fragments=DEFAULT_MAX_FRAGMENTS,
        )
    return RagSettingsResponse(
        relevance_threshold=row.relevance_threshold,
        max_fragments=row.max_fragments,
    )


@router.put("", response_model=RagSettingsResponse)
async def update_rag_settings(
    body: RagSettingsUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> RagSettingsResponse:
    workspace_id = request.app.state.workspace_id
    if not await _check_manage_corpora(session, user):
        raise NotFound(
            constraint={"object": "rag-settings", "reason": "manage_corpora required"},
            hint="Нет права на управление корпусами",
        )

    result = await session.execute(
        select(RagSettings).where(RagSettings.workspace_id == workspace_id)
    )
    row = result.scalar_one_or_none()
    old_threshold = row.relevance_threshold if row else DEFAULT_RELEVANCE_THRESHOLD
    old_max = row.max_fragments if row else DEFAULT_MAX_FRAGMENTS

    if body.relevance_threshold == old_threshold and body.max_fragments == old_max:
        return RagSettingsResponse(
            relevance_threshold=old_threshold,
            max_fragments=old_max,
        )

    if row is None:
        row = RagSettings(
            workspace_id=workspace_id,
            relevance_threshold=body.relevance_threshold,
            max_fragments=body.max_fragments,
        )
        session.add(row)
    else:
        row.relevance_threshold = body.relevance_threshold
        row.max_fragments = body.max_fragments

    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=user.id,
        action="rag_settings.changed",
        object_type="rag_settings",
        object_id=workspace_id,
        meta={
            "old": {
                "relevance_threshold": old_threshold,
                "max_fragments": old_max,
            },
            "new": {
                "relevance_threshold": body.relevance_threshold,
                "max_fragments": body.max_fragments,
            },
        },
    )

    await session.commit()
    return RagSettingsResponse(
        relevance_threshold=body.relevance_threshold,
        max_fragments=body.max_fragments,
    )
