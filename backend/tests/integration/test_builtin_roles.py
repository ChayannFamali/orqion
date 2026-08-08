"""Тест идемпотентного пересева встроенных ролей."""

from __future__ import annotations

import pytest
from app.auth.bootstrap import ensure_builtin_roles
from app.db.models import Role
from app.db.workspace import ensure_default_workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_builtin_roles_created_on_first_call(db_session: AsyncSession) -> None:
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    await ensure_builtin_roles(db_session, ws_id)
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.workspace_id == ws_id, Role.is_builtin.is_(True))
    )
    roles = {r.name: r for r in result.scalars().all()}
    assert set(roles.keys()) == {"support", "developer", "architect", "manager", "admin"}
    assert roles["admin"].policy["models"] == ["*"]


@pytest.mark.asyncio
async def test_builtin_roles_reseeded_on_second_call(db_session: AsyncSession) -> None:
    """При повторном старте политика builtin-ролей восстанавливается из пресетов."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    await ensure_builtin_roles(db_session, ws_id)
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.workspace_id == ws_id, Role.name == "support")
    )
    support = result.scalar_one()
    support.policy = {"models": ["hacked"], "max_input_tokens": 999999}
    await db_session.flush()

    await ensure_builtin_roles(db_session, ws_id)
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.workspace_id == ws_id, Role.name == "support")
    )
    support = result.scalar_one()
    assert support.policy["models"] == ["local/*"]
    assert support.policy["max_input_tokens"] == 16000


@pytest.mark.asyncio
async def test_custom_roles_not_touched_by_reseed(db_session: AsyncSession) -> None:
    """Пользовательские роли (is_builtin=False) не затрагиваются пересевом."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    await ensure_builtin_roles(db_session, ws_id)
    await db_session.flush()

    custom = Role(
        workspace_id=ws_id,
        name="intern",
        is_builtin=False,
        policy={"models": ["local/qwen3-4b"], "max_input_tokens": 4000},
    )
    db_session.add(custom)
    await db_session.flush()

    await ensure_builtin_roles(db_session, ws_id)
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.workspace_id == ws_id, Role.name == "intern")
    )
    intern = result.scalar_one()
    assert intern.policy["models"] == ["local/qwen3-4b"]
    assert intern.is_builtin is False
