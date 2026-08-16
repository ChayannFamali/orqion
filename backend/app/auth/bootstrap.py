"""Создание первого администратора и встроенных ролей при первом старте.

Пароль генерируется и выводится в stdout однократно.
В логи (stderr/JSON) пароль не попадает никогда (AGENTS.md §14).
Встроенные роли создаются идемпотентно: при первом старте создаются из пресетов,
при последующих — НЕ перезаписываются (arch.md §5.2: ролевая модель меняется
миграцией данных, а не схемы). Изменения, внесённые через API, сохраняются.
"""

from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import generate_random_password, hash_password
from app.db.models import Role, User
from app.policy.presets import BUILTIN_ROLES


async def ensure_builtin_roles(session: AsyncSession, workspace_id: str) -> None:
    """Создаёт встроенные роли с эталонной политикой при первом старте.

    Идемпотентно: если builtin-роль уже существует, её политика НЕ перезаписывается.
    Изменения, внесённые администратором через API, сохраняются после рестарта.
    Пользовательские роли (is_builtin=False) не затрагиваются.
    """
    result = await session.execute(
        select(Role).where(
            Role.workspace_id == workspace_id,
            Role.is_builtin.is_(True),
        )
    )
    existing = {r.name: r for r in result.scalars().all()}

    for name, policy in BUILTIN_ROLES.items():
        if name not in existing:
            role = Role(
                workspace_id=workspace_id,
                name=name,
                is_builtin=True,
                policy=policy.model_dump(),
            )
            session.add(role)
    await session.flush()


async def ensure_initial_admin(session: AsyncSession, workspace_id: str) -> bool:
    """Создаёт пользователя admin@orqion.local при первом старте.

    Роль admin создаётся через ensure_builtin_roles.
    Возвращает True, если пользователь был создан (пароль выведен в stdout).
    Возвращает False, если пользователь уже существует.
    """
    await ensure_builtin_roles(session, workspace_id)

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

    result = await session.execute(
        select(Role).where(
            Role.workspace_id == workspace_id,
            Role.name == "admin",
            Role.is_builtin.is_(True),
        )
    )
    role = result.scalar_one()

    password = generate_random_password()
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
