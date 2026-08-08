"""resolve_policy: единственная функция, читающая роль пользователя."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, User
from app.policy.models import Policy


async def resolve_policy(session: AsyncSession, user: User) -> Policy:
    """Читает роль пользователя и возвращает Policy.

    Единственное место в коде, где существует понятие роли (ADR-4, S-11).
    """
    result = await session.execute(select(Role).where(Role.id == user.role_id))
    role = result.scalar_one()
    return Policy.model_validate(role.policy)
