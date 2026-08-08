"""Тесты аутентификации: login, logout, me, истёкшая сессия → 401."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Role, Session, User, Workspace
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_test_user(session: AsyncSession) -> tuple[User, str]:
    """Создаёт workspace, role, user. Возвращает (user, plaintext_password)."""
    ws = Workspace(name="test")
    session.add(ws)
    await session.flush()

    role = Role(workspace_id=ws.id, name="member", is_builtin=False, policy={})
    session.add(role)
    await session.flush()

    password = "test-password-123"
    user = User(
        workspace_id=ws.id,
        email="user@orqion.local",
        password_hash=hash_password(password),
        role_id=role.id,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user, password


@pytest.mark.asyncio
async def test_login_success(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Login возвращает 200, cookie установлена, /me работает."""
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        _, password = await _seed_test_user(session)
        await session.commit()

    response = await api_client.post(
        "/api/auth/login",
        json={"email": "user@orqion.local", "password": password},
    )
    assert response.status_code == 200
    assert response.json()["user"]["email"] == "user@orqion.local"
    assert COOKIE_NAME in response.cookies

    me_response = await api_client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "user@orqion.local"


@pytest.mark.asyncio
async def test_login_wrong_password(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        await _seed_test_user(session)
        await session.commit()

    response = await api_client.post(
        "/api/auth/login",
        json={"email": "user@orqion.local", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_credentials"
    assert COOKIE_NAME not in response.cookies


@pytest.mark.asyncio
async def test_me_without_cookie(api_client: httpx.AsyncClient) -> None:
    """GET /api/auth/me без cookie → 401."""
    response = await api_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["error"] == "authentication_required"


@pytest.mark.asyncio
async def test_logout_invalidates_session(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Logout удаляет сессию; последующий /me → 401."""
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        _, password = await _seed_test_user(session)
        await session.commit()

    response = await api_client.post(
        "/api/auth/login",
        json={"email": "user@orqion.local", "password": password},
    )
    assert response.status_code == 200
    session_cookie = response.cookies[COOKIE_NAME]

    logout_response = await api_client.post("/api/auth/logout")
    assert logout_response.status_code == 204

    api_client.cookies.clear()
    api_client.cookies.set(COOKIE_NAME, session_cookie)
    me_response = await api_client.get("/api/auth/me")
    assert me_response.status_code == 401


@pytest.mark.asyncio
async def test_expired_session_returns_401(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Истёкшая сессия → 401."""
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user, _ = await _seed_test_user(session)
        await session.commit()

        session_id = await create_session(session, user.id, user.workspace_id, Settings())

        from sqlalchemy import update

        await session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(expires_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    me_response = await api_client.get("/api/auth/me")
    assert me_response.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Неактивный пользователь не может войти."""
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user, password = await _seed_test_user(session)
        from sqlalchemy import update

        await session.execute(update(User).where(User.id == user.id).values(is_active=False))
        await session.commit()

    response = await api_client.post(
        "/api/auth/login",
        json={"email": "user@orqion.local", "password": password},
    )
    assert response.status_code == 401
