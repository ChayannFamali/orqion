"""Impersonate: вход от имени другого пользователя.

Доступно только администратору. Записывается в audit_log (arch.md §3).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit
from app.auth.sessions import create_session
from app.config import Settings
from app.db.models import Role, User
from app.errors import NotFound, OrqionError


class ImpersonationDenied(OrqionError):
    error_code = "impersonation_denied"
    reason = "Только администратор может войти от имени другого пользователя"
    status_code = 403


async def impersonate(
    session: AsyncSession,
    admin_user: User,
    target_user_id: str,
    workspace_id: str,
    settings: Settings,
) -> str:
    """Создаёт сессию от имени target_user. Возвращает session_id.

    Проверяет, что admin_user имеет роль admin (is_builtin).
    Записывает действие в audit_log.
    """
    role_result = await session.execute(select(Role).where(Role.id == admin_user.role_id))
    role = role_result.scalar_one()
    if not (role.is_builtin and role.name == "admin"):
        raise ImpersonationDenied()

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

    session_id = await create_session(session, target.id, workspace_id, settings)

    await write_audit(
        session,
        workspace_id=workspace_id,
        actor_user_id=admin_user.id,
        action="impersonate",
        object_type="user",
        object_id=target.id,
        meta={"target_email": target.email},
    )

    return session_id
