"""Чтение роли пользователя: единственный источник `Role` для маршрутов."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Role, User
from app.policy.models import Policy


async def resolve_role(session: AsyncSession, user: User) -> Role:
    """Читает роль пользователя.

    Единственное место в коде, где существует понятие роли (ADR-4, S-11).
    """
    result = await session.execute(select(Role).where(Role.id == user.role_id))
    return result.scalar_one()


def policy_of(role: Role) -> Policy:
    """Policy записи роли.

    Единственный перевод `role.policy` в модель: маршрут, которому нужны и
    роль, и политика, берёт их здесь, а не повторяет `model_validate` сам.
    """
    return Policy.model_validate(role.policy)


async def resolve_policy(session: AsyncSession, user: User) -> Policy:
    """Читает роль пользователя и возвращает Policy."""
    return policy_of(await resolve_role(session, user))
