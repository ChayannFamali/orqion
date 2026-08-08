"""Тест моделей user/role/session: создание, индексы, FK, roundtrip миграции."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.db.models import Role, Session, User, Workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_role_user_session_create_chain(db_session: AsyncSession) -> None:
    """Role → User → Session создаются и читаются."""
    ws = Workspace(name="test")
    db_session.add(ws)
    await db_session.flush()

    role = Role(
        workspace_id=ws.id,
        name="admin",
        is_builtin=True,
        policy={"models": ["local/*"], "max_input_tokens": 16000},
    )
    db_session.add(role)
    await db_session.flush()

    user = User(
        workspace_id=ws.id,
        email="admin@orqion.local",
        password_hash="$argon2id$stub",
        role_id=role.id,
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    session = Session(
        workspace_id=ws.id,
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    db_session.add(session)
    await db_session.flush()

    result = await db_session.execute(select(Session).where(Session.user_id == user.id))
    sessions = result.scalars().all()
    assert len(sessions) == 1
    assert sessions[0].id == session.id


@pytest.mark.asyncio
async def test_user_email_unique_per_workspace(db_session: AsyncSession) -> None:
    """Два пользователя с одинаковым email в одном workspace — ошибка."""
    from sqlalchemy.exc import IntegrityError

    ws = Workspace(name="test")
    db_session.add(ws)
    await db_session.flush()

    role = Role(workspace_id=ws.id, name="admin", is_builtin=True, policy={})
    db_session.add(role)
    await db_session.flush()

    user1 = User(
        workspace_id=ws.id,
        email="dup@orqion.local",
        password_hash="$argon2id$stub1",
        role_id=role.id,
    )
    db_session.add(user1)
    await db_session.flush()

    user2 = User(
        workspace_id=ws.id,
        email="dup@orqion.local",
        password_hash="$argon2id$stub2",
        role_id=role.id,
    )
    db_session.add(user2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_user_role_fk_enforced(db_session: AsyncSession) -> None:
    """User с несуществующим role_id — ошибка FK."""
    from sqlalchemy.exc import IntegrityError

    ws = Workspace(name="test")
    db_session.add(ws)
    await db_session.flush()

    user = User(
        workspace_id=ws.id,
        email="nofk@orqion.local",
        role_id="nonexistent-role-id",
    )
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_indexes_exist_on_all_tables(db_session: AsyncSession) -> None:
    """Индексы по workspace_id и email присутствуют."""

    def _check_indexes(sync_session: object) -> dict[str, set[str]]:
        from sqlalchemy import inspect as sync_inspect

        engine = sync_session.bind  # type: ignore[attr-defined]
        insp = sync_inspect(engine)
        assert insp is not None
        return {
            "role_indexes": {idx["name"] for idx in insp.get_indexes("role")},
            "user_indexes": {idx["name"] for idx in insp.get_indexes("user")},
            "session_indexes": {idx["name"] for idx in insp.get_indexes("session")},
            "user_uniques": {u["name"] for u in insp.get_unique_constraints("user")},
        }

    result = await db_session.run_sync(_check_indexes)

    assert "ix_role_workspace_id" in result["role_indexes"]
    assert "ix_user_workspace_id" in result["user_indexes"]
    assert "ix_session_workspace_id" in result["session_indexes"]
    assert "ix_session_user_id" in result["session_indexes"]
    assert "uq_user_workspace_email" in result["user_uniques"]
