"""Диагностика окружения хоста (T-444, T-511): только чтение, без управления.

Раздел намеренно read-only (arch.md §14.3): версии драйверов и метрики
железа — информация, не действия; кнопок «скачать»/«установить» нет.

Ответ собирает четыре группы фактов: GPU, хост (ОС, Python, аптайм,
свободное место томов хранения), внешние сервисы (статус из накопленного
результата зонда провайдеров — своих сетевых запросов раздел не делает) и
локальные компоненты (пакеты и файлы хранилищ). Каждый пункт деградирует
самостоятельно: недоступность одного не валит остальные.

Access control: capability "view_diagnostics" — по умолчанию только admin
через "*" (в seed-пресеты не добавляется: раскрываются версии драйверов
хоста). Паттерн гейта — _check_manage_providers (T-308): без права 404,
существование раздела не раскрывается.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
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
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> EnvironmentDiagnosticsResponse:
    if not await _check_view_diagnostics(session, user):
        raise NotFound(
            constraint={"object": "diagnostics", "reason": "view_diagnostics required"},
            hint="Нет права на просмотр диагностики окружения",
        )
    # started_at кладёт lifespan; при сборке приложения без него (тесты, CLI)
    # значения нет — раздел честно отдаёт null, а не ноль.
    return await collect_environment_diagnostics(
        settings=request.app.state.settings,
        session=session,
        workspace_id=user.workspace_id,
        started_at=getattr(request.app.state, "started_at", None),
    )
