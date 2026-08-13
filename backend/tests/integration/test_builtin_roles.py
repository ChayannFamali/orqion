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
async def test_builtin_roles_preserve_edited_policy_on_reseed(db_session: AsyncSession) -> None:
    """При повторном старте политика builtin-ролей НЕ перезаписывается.

    arch.md §5.2: ролевая модель меняется миграцией данных, а не схемы.
    Изменения, внесённые администратором через API, сохраняются после рестарта.
    """
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    await ensure_builtin_roles(db_session, ws_id)
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.workspace_id == ws_id, Role.name == "support")
    )
    support = result.scalar_one()
    support.policy = {"models": ["local/custom-model"], "max_input_tokens": 999999}
    await db_session.flush()

    await ensure_builtin_roles(db_session, ws_id)
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.workspace_id == ws_id, Role.name == "support")
    )
    support = result.scalar_one()
    assert support.policy["models"] == ["local/custom-model"]
    assert support.policy["max_input_tokens"] == 999999


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


@pytest.mark.asyncio
async def test_ensure_builtin_roles_idempotent_preserves_all_edits(
    db_session: AsyncSession,
) -> None:
    """Полный цикл: создание → редактирование всех 5 ролей → повторный вызов → все правки сохранены.

    Документирует контракт: ensure_builtin_roles создаёт роли при первом старте,
    но никогда не перезаписывает политику существующих ролей. Это защита от
    повторного появления бага перезаписи при рефакторинге bootstrap-кода.
    """
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    await ensure_builtin_roles(db_session, ws_id)
    await db_session.flush()

    edited_policy = {
        "models": ["local/edited"],
        "max_input_tokens": 12345,
        "max_output_tokens": 6789,
        "reasoning": "on",
        "budget": {"tokens_month": 111, "cost_month": 222},
        "rpm": 99,
        "tpm": 999,
        "corpora": ["edited"],
        "capabilities": ["chat"],
    }

    result = await db_session.execute(
        select(Role).where(Role.workspace_id == ws_id, Role.is_builtin.is_(True))
    )
    for role in result.scalars().all():
        role.policy = dict(edited_policy)
    await db_session.flush()

    await ensure_builtin_roles(db_session, ws_id)
    await db_session.flush()

    result = await db_session.execute(
        select(Role).where(Role.workspace_id == ws_id, Role.is_builtin.is_(True))
    )
    for role in result.scalars().all():
        assert role.policy["models"] == ["local/edited"], f"{role.name} policy was overwritten"
        assert (
            role.policy["max_input_tokens"] == 12345
        ), f"{role.name} max_input_tokens was overwritten"
        assert role.policy["capabilities"] == ["chat"], f"{role.name} capabilities were overwritten"
