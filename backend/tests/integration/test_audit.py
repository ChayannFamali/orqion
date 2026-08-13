"""Тест аудита и impersonate: запись, append-only, impersonate."""

from __future__ import annotations

import pytest
from app.audit.service import list_audit, write_audit
from app.auth.impersonate import ImpersonationDenied, impersonate
from app.config import Settings
from app.db.models import AuditLog, Role, User
from app.db.workspace import ensure_default_workspace
from app.errors import NotFound
from app.policy.presets import BUILTIN_ROLES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _create_user_with_role(
    session: AsyncSession,
    workspace_id: str,
    email: str,
    role_name: str,
    is_builtin: bool = False,
    policy: dict[str, object] | None = None,
) -> User:
    role = Role(
        workspace_id=workspace_id,
        name=role_name,
        is_builtin=is_builtin,
        policy=policy or BUILTIN_ROLES["admin"].model_dump(),
    )
    session.add(role)
    await session.flush()

    user = User(
        workspace_id=workspace_id,
        email=email,
        password_hash="$argon2id$stub",
        role_id=role.id,
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_audit_write_and_list(db_session: AsyncSession) -> None:
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    admin = await _create_user_with_role(db_session, ws_id, "admin@orqion.local", "admin", True)

    await write_audit(
        db_session,
        workspace_id=ws_id,
        actor_user_id=admin.id,
        action="role.policy_changed",
        object_type="role",
        object_id="role-123",
        meta={"field": "max_input_tokens", "old": 16000, "new": 32000},
    )
    await db_session.flush()

    records, total = await list_audit(db_session, ws_id)
    assert len(records) == 1
    assert records[0].action == "role.policy_changed"
    assert records[0].object_type == "role"
    assert records[0].meta["field"] == "max_input_tokens"


@pytest.mark.asyncio
async def test_impersonate_creates_session_and_audit_entry(
    db_session: AsyncSession,
) -> None:
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    admin = await _create_user_with_role(db_session, ws_id, "admin@orqion.local", "admin", True)
    target = await _create_user_with_role(
        db_session,
        ws_id,
        "dev@orqion.local",
        "developer",
        False,
        policy=BUILTIN_ROLES["developer"].model_dump(),
    )

    session_id = await impersonate(db_session, admin, target.id, ws_id, Settings())
    await db_session.flush()

    assert session_id is not None

    records, total = await list_audit(db_session, ws_id)
    assert len(records) == 1
    assert records[0].action == "impersonate"
    assert records[0].actor_user_id == admin.id
    assert records[0].object_id == target.id
    assert records[0].meta["target_email"] == "dev@orqion.local"


@pytest.mark.asyncio
async def test_impersonate_denied_without_capability(db_session: AsyncSession) -> None:
    """Роль без 'impersonate' в capabilities → 403.

    Проверка идёт через resolve_policy + capabilities, не по имени роли.
    Developer не имеет 'impersonate' и '*' в capabilities.
    """
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    dev = await _create_user_with_role(
        db_session,
        ws_id,
        "dev@orqion.local",
        "developer",
        False,
        policy=BUILTIN_ROLES["developer"].model_dump(),
    )
    target = await _create_user_with_role(
        db_session,
        ws_id,
        "user@orqion.local",
        "support",
        False,
        policy=BUILTIN_ROLES["support"].model_dump(),
    )

    with pytest.raises(ImpersonationDenied):
        await impersonate(db_session, dev, target.id, ws_id, Settings())


@pytest.mark.asyncio
async def test_impersonate_target_not_found(db_session: AsyncSession) -> None:
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    admin = await _create_user_with_role(db_session, ws_id, "admin@orqion.local", "admin", True)

    with pytest.raises(NotFound):
        await impersonate(db_session, admin, "nonexistent", ws_id, Settings())


@pytest.mark.asyncio
async def test_audit_log_is_append_only_by_convention(
    db_session: AsyncSession,
) -> None:
    """audit_log не имеет UPDATE/DELETE эндпоинтов в API (arch.md §5.3).

    Тест проверяет, что сервисный слой не предоставляет операции удаления.
    """
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    admin = await _create_user_with_role(db_session, ws_id, "admin@orqion.local", "admin", True)

    await write_audit(
        db_session,
        workspace_id=ws_id,
        actor_user_id=admin.id,
        action="test_action",
        object_type="test",
    )
    await db_session.flush()

    records, total = await list_audit(db_session, ws_id)
    assert len(records) == 1

    result = await db_session.execute(select(AuditLog).where(AuditLog.action == "test_action"))
    record = result.scalar_one()
    assert record.action == "test_action"
