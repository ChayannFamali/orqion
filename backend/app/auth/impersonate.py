"""Impersonate: вход от имени другого пользователя.

Доступ проверяется через capabilities в Policy (arch.md §5.2), не напрямую
по имени роли (AGENTS.md §5.2). Admin получает право через capabilities=["*"].
Записывается в audit_log (arch.md §3).

Блокировка вложенной имперсонации: если текущая сессия уже имеет impersonated_by,
повторный вызов отклоняется (сначала выйдите из текущей имперсонации).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit
from app.auth.sessions import create_session, get_session_record
from app.config import Settings
from app.db.models import User
from app.errors import BadRequest, NotFound, OrqionError
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy


class ImpersonationDenied(OrqionError):
    error_code = "impersonation_denied"
    reason = "У вас нет права входить от имени другого пользователя"
    status_code = 403
    hint = "Обратитесь к администратору"


async def impersonate(
    session: AsyncSession,
    actor: User,
    target_user_id: str,
    workspace_id: str,
    settings: Settings,
    actor_session_id: str | None = None,
) -> str:
    """Создаёт сессию от имени target_user. Возвращает session_id.

    Проверка права — через resolve_policy(actor).capabilities.
    Записывает действие в audit_log.

    actor_session_id — ID текущей сессии админа (для impersonated_by и блокировки
    вложенной имперсонации). Если None, impersonated_by не устанавливается.
    """
    policy = await resolve_policy(session, actor)
    if WILDCARD not in policy.capabilities and "impersonate" not in policy.capabilities:
        raise ImpersonationDenied()

    # Блокировка вложенной имперсонации
    if actor_session_id is not None:
        current_session = await get_session_record(session, actor_session_id)
        if current_session is not None and current_session.impersonated_by is not None:
            raise BadRequest(
                "Невозможно начать новую имперсонацию, не завершив текущую",
                hint="Сначала выйдите из текущей имперсонации",
            )

    user_result = await session.execute(
        select(User).where(
            User.id == target_user_id,
            User.workspace_id == workspace_id,
            User.is_active.is_(True),
        )
    )
    target = user_result.scalar_one_or_none()
    if target is None:
        raise NotFound(
            constraint={"object": "user", "id": target_user_id},
            hint="Пользователь не найден или неактивен",
        )

    session_id = await create_session(
        session,
        target.id,
        workspace_id,
        settings,
        impersonated_by=actor_session_id,
    )

    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=actor.id,
        action="impersonate",
        object_type="user",
        object_id=target.id,
        meta={"target_email": target.email},
    )

    return session_id
