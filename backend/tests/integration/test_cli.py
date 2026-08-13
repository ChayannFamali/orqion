"""Тесты CLI: createuser, reset-password.

Проверки:
- createuser создаёт пользователя с указанной ролью
- createuser с дубликатом email → ошибка
- createuser с несуществующей ролью → ошибка
- reset-password сбрасывает пароль
- reset-password несуществующего пользователя → ошибка
- сгенерированный пароль выводится в stdout
"""

from __future__ import annotations

import os
import tempfile

import pytest
from app.auth.bootstrap import ensure_builtin_roles
from app.auth.passwords import verify_password
from app.config import Settings
from app.db.base import Base
from app.db.engine import create_engine, create_session_factory
from app.db.models import Role, User
from app.db.workspace import ensure_default_workspace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def _make_test_settings(db_path: str) -> Settings:
    return Settings(database_url=f"sqlite:///{db_path}")


async def _setup_test_db(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Создаёт тестовую БД с workspace и builtin roles."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        ws_id = await ensure_default_workspace(session)
        await ensure_builtin_roles(session, ws_id)
        await session.commit()

    return engine, session_factory


@pytest.mark.asyncio
async def test_createuser_creates_new_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """createuser создаёт пользователя с указанной ролью."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, session_factory = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_createuser

    await _run_createuser("dev@orqion.local", "developer", "test-password-123")

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == "dev@orqion.local"))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.is_active is True
        assert user.password_hash is not None
        assert verify_password(user.password_hash, "test-password-123") is True

        role = await session.get(Role, user.role_id)
        assert role is not None
        assert role.name == "developer"

    await engine.dispose()


@pytest.mark.asyncio
async def test_createuser_duplicate_email(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """createuser с дубликатом email → ошибка в stderr."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, session_factory = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_createuser

    await _run_createuser("dup@orqion.local", "developer", "pass1")
    with pytest.raises(SystemExit) as exc_info:
        await _run_createuser("dup@orqion.local", "developer", "pass2")
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "already exists" in captured.err

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == "dup@orqion.local"))
        users = result.scalars().all()
        assert len(users) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_createuser_nonexistent_role(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """createuser с несуществующей ролью → ошибка."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, session_factory = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_createuser

    with pytest.raises(SystemExit) as exc_info:
        await _run_createuser("x@orqion.local", "nonexistent-role", "pass")
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "not found" in captured.err

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == "x@orqion.local"))
        assert result.scalar_one_or_none() is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_createuser_generated_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """createuser без --password генерирует пароль и выводит в stdout."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, session_factory = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_createuser

    await _run_createuser("gen@orqion.local", "developer", None)

    captured = capsys.readouterr()
    assert "Password:" in captured.out
    assert "Save this password" in captured.out

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == "gen@orqion.local"))
        user = result.scalar_one_or_none()
        assert user is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_reset_password_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """reset-password сбрасывает пароль существующего пользователя."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, session_factory = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_createuser, _run_reset_password

    await _run_createuser("reset@orqion.local", "developer", "old-password")
    await _run_reset_password("reset@orqion.local", "new-password-456")

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == "reset@orqion.local"))
        user = result.scalar_one()
        assert user.password_hash is not None
        assert verify_password(user.password_hash, "old-password") is False
        assert verify_password(user.password_hash, "new-password-456") is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_reset_password_nonexistent_user(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """reset-password несуществующего пользователя → ошибка."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, _sf = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_reset_password

    with pytest.raises(SystemExit) as exc_info:
        await _run_reset_password("ghost@orqion.local", "pass")
    assert exc_info.value.code == 1

    captured = capsys.readouterr()
    assert "not found" in captured.err

    await engine.dispose()


@pytest.mark.asyncio
async def test_reset_password_generated(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """reset-password без --password генерирует пароль и выводит в stdout."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, _sf = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_createuser, _run_reset_password

    await _run_createuser("gen2@orqion.local", "developer", "old")
    await _run_reset_password("gen2@orqion.local", None)

    captured = capsys.readouterr()
    assert "Password:" in captured.out
    assert "Save this password" in captured.out

    await engine.dispose()


@pytest.mark.asyncio
async def test_reset_password_revokes_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """reset-password отзывает все активные сессии пользователя."""
    from app.auth.sessions import create_session
    from app.db.models import Session as SessionModel

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    settings = _make_test_settings(db_path)
    engine, session_factory = await _setup_test_db(settings)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)

    from app.cli import _run_createuser, _run_reset_password

    await _run_createuser("revoke@orqion.local", "developer", "old-pass")

    # Создаём сессию для пользователя
    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email == "revoke@orqion.local"))
        user = result.scalar_one()
        await create_session(session, user.id, user.workspace_id, settings)
        await session.commit()

    # Проверяем, что сессия существует
    async with session_factory() as session:
        result = await session.execute(select(SessionModel).where(SessionModel.user_id == user.id))
        assert len(result.scalars().all()) == 1

    # Сбрасываем пароль
    await _run_reset_password("revoke@orqion.local", "new-pass")

    # Проверяем, что сессия отозвана
    async with session_factory() as session:
        result = await session.execute(select(SessionModel).where(SessionModel.user_id == user.id))
        assert len(result.scalars().all()) == 0

    await engine.dispose()
