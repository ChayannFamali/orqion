"""Диагностика окружения хоста (T-444): только чтение, без управления.

Раздел намеренно read-only (arch.md §14.3): версии драйверов и метрики
железа — информация, не действия; кнопок «скачать»/«установить» нет.

Access control: capability "view_diagnostics" — по умолчанию только admin
через "*" (в seed-пресеты не добавляется: раскрываются версии драйверов
хоста). Паттерн гейта — _check_manage_providers (T-308): без права 404,
существование раздела не раскрывается.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.diagnostics import EnvironmentDiagnosticsResponse
from app.auth.dependencies import current_user
from app.db.models import User
from app.db.session import get_session
from app.diagnostics import collect_environment_diagnostics
from app.errors import NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/diagnostics",
    tags=["diagnostics"],
    dependencies=[Depends(current_user)],
)


async def _check_view_diagnostics(session: AsyncSession, user: User) -> bool:
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities or "view_diagnostics" in policy.capabilities


@router.get("/environment", response_model=EnvironmentDiagnosticsResponse)
async def get_environment_diagnostics(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> EnvironmentDiagnosticsResponse:
    if not await _check_view_diagnostics(session, user):
        raise NotFound(
            constraint={"object": "diagnostics", "reason": "view_diagnostics required"},
            hint="Нет права на просмотр диагностики окружения",
        )
    return await collect_environment_diagnostics()
