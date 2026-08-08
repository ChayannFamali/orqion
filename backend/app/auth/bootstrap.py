"""Создание первого администратора при первом старте.

Пароль генерируется и выводится в stdout однократно.
В логи (stderr/JSON) пароль не попадает никогда (AGENTS.md §14).
"""

from __future__ import annotations

import secrets
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.db.models import Role, User


async def ensure_initial_admin(session: AsyncSession, workspace_id: str) -> bool:
    """Создаёт роль admin и пользователя admin@orqion.local при первом старте.

    Возвращает True, если администратор был создан (пароль выведен в stdout).
    Возвращает False, если администратор уже существует.
    """
    result = await session.execute(
        select(User)
        .join(Role, User.role_id == Role.id)
        .where(
            User.workspace_id == workspace_id,
            Role.name == "admin",
            Role.is_builtin.is_(True),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return False

    role = Role(
        workspace_id=workspace_id,
        name="admin",
        is_builtin=True,
        policy={},
    )
    session.add(role)
    await session.flush()

    password = secrets.token_urlsafe(16)
    user = User(
        workspace_id=workspace_id,
        email="admin@orqion.local",
        password_hash=hash_password(password),
        role_id=role.id,
        is_active=True,
    )
    session.add(user)
    await session.flush()

    print(
        f"\n=== orqion: initial admin created ===\n"
        f"Email: admin@orqion.local\n"
        f"Password: {password}\n"
        f"=== Save this password. It will not be shown again. ===\n",
        file=sys.stdout,
        flush=True,
    )
    return True
