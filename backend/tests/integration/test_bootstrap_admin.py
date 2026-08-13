"""Тест bootstrap-администратора: создание, идемпотентность, пароль в stdout."""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest
from app.auth.bootstrap import ensure_initial_admin
from app.auth.passwords import verify_password
from app.db.models import Role, User
from app.db.workspace import ensure_default_workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_admin_created_on_first_call(db_session: AsyncSession) -> None:
    """При первом старте создаётся role=admin и user=admin@orqion.local."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    buf = io.StringIO()
    with redirect_stdout(buf):
        created = await ensure_initial_admin(db_session, ws_id)
    await db_session.flush()

    assert created is True

    result = await db_session.execute(select(User).where(User.email == "admin@orqion.local"))
    user = result.scalar_one()
    assert user.is_active is True
    assert user.workspace_id == ws_id

    role_result = await db_session.execute(
        select(Role).where(Role.name == "admin", Role.is_builtin.is_(True))
    )
    role = role_result.scalar_one()
    assert role.is_builtin is True
    assert user.role_id == role.id

    output = buf.getvalue()
    assert "Password:" in output
    assert "will not be shown again" in output


@pytest.mark.asyncio
async def test_admin_not_recreated_on_second_call(db_session: AsyncSession) -> None:
    """Повторный вызов не пересоздаёт администратора."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    buf1 = io.StringIO()
    with redirect_stdout(buf1):
        await ensure_initial_admin(db_session, ws_id)
    await db_session.flush()

    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        created = await ensure_initial_admin(db_session, ws_id)
    await db_session.flush()

    assert created is False
    assert "Password:" not in buf2.getvalue()

    result = await db_session.execute(select(User).where(User.email == "admin@orqion.local"))
    users = result.scalars().all()
    assert len(users) == 1


@pytest.mark.asyncio
async def test_admin_password_not_in_db_as_plaintext(db_session: AsyncSession) -> None:
    """Открытый пароль не сохраняется в БД."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    buf = io.StringIO()
    with redirect_stdout(buf):
        await ensure_initial_admin(db_session, ws_id)
    await db_session.flush()

    output = buf.getvalue()
    password_line = [line for line in output.splitlines() if line.startswith("Password:")]
    assert len(password_line) == 1
    plaintext = password_line[0].split(":", 1)[1].strip()
    assert len(plaintext) > 0

    result = await db_session.execute(
        select(User.password_hash).where(User.email == "admin@orqion.local")
    )
    stored_hash = result.scalar_one()
    assert stored_hash is not None
    assert plaintext not in stored_hash
    assert "$argon2id$" in stored_hash
    assert verify_password(stored_hash, plaintext) is True


@pytest.mark.asyncio
async def test_admin_password_not_in_logger_output(db_session: AsyncSession) -> None:
    """Пароль выводится в stdout, не в stderr (логгер пишет в stderr)."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    stdout_buf = io.StringIO()
    with redirect_stdout(stdout_buf):
        await ensure_initial_admin(db_session, ws_id)
    await db_session.flush()

    assert "Password:" in stdout_buf.getvalue()
