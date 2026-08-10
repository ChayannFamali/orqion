"""Integration-тест: троттлинг /api/auth/login (T-108a).

Проверяет, что после N неудачных попыток → 429 login_rate_limited.
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.db.models import Role, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI


async def _setup_user(app_fixture: FastAPI) -> None:
    """Создаёт пользователя для тестов входа."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name="developer",
            is_builtin=True,
            policy=BUILTIN_ROLES["developer"].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="login-test@orqion.local",
            password_hash=hash_password("correct-password-123"),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()


@pytest.mark.asyncio
async def test_login_rate_limited_after_max_attempts(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """После max_attempts неудачных попыток → 429 login_rate_limited."""
    await _setup_user(app_fixture)

    # Устанавливаем маленький лимит для теста
    from app.auth.rate_limit import LoginRateLimiter

    app_fixture.state.login_rate_limiter = LoginRateLimiter(max_attempts=3, period_seconds=300)

    # Делаем 3 неудачные попытки (неверный пароль) → все 401
    for i in range(3):
        response = await api_client.post(
            "/api/auth/login",
            json={"email": "login-test@orqion.local", "password": "wrong"},
        )
        assert response.status_code == 401, (
            f"Attempt {i + 1}: expected 401, got {response.status_code}"
        )

    # 4-я попытка → 429
    response = await api_client.post(
        "/api/auth/login",
        json={"email": "login-test@orqion.local", "password": "wrong"},
    )
    assert response.status_code == 429
    body = response.json()
    assert body["error"] == "login_rate_limited"
    assert "reset_in_seconds" in body["constraint"]


@pytest.mark.asyncio
async def test_successful_login_resets_rate_limit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Успешный вход сбрасывает счётчик — дальнейшие попытки не блокируются."""
    await _setup_user(app_fixture)
    app_fixture.state.login_rate_limiter.__init__(max_attempts=3, period_seconds=300)

    # 2 неудачные попытки
    for _ in range(2):
        await api_client.post(
            "/api/auth/login",
            json={"email": "login-test@orqion.local", "password": "wrong"},
        )

    # Успешный вход
    response = await api_client.post(
        "/api/auth/login",
        json={"email": "login-test@orqion.local", "password": "correct-password-123"},
    )
    assert response.status_code == 200

    # Счётчик сброшен — снова 3 неудачные попытки → все 401
    for _ in range(3):
        response = await api_client.post(
            "/api/auth/login",
            json={"email": "login-test@orqion.local", "password": "wrong"},
        )
        assert response.status_code == 401

    # 4-я → 429
    response = await api_client.post(
        "/api/auth/login",
        json={"email": "login-test@orqion.local", "password": "wrong"},
    )
    assert response.status_code == 429
