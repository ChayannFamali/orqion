"""T-404a: IdentityProvider interface + LocalIdentityProvider tests.

Проверки:
- IdentityProvider Protocol существует
- LocalIdentityProvider аутентифицирует по email+password
- Неверный пароль → InvalidCredentials (401)
- Несуществующий email → InvalidCredentials (401)
- password_hash=None (OIDC-only user) → локальный вход отклонён
- auth_method поле: default="local" при создании через CLI/bootstrap
- external_subject/external_issuer nullable
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.local_provider import InvalidCredentials, LocalIdentityProvider
from app.auth.passwords import hash_password
from app.auth.provider import AuthResult, IdentityProvider
from app.db.models import Role, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI
from sqlalchemy import select


async def _seed_user(
    app_fixture: FastAPI,
    email: str = "test@orqion.local",
    role_name: str = "developer",
    password: str = "pass-123",
    password_hash: str | None = None,
    auth_method: str = "local",
) -> User:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name=role_name,
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email=email,
            password_hash=password_hash or hash_password(password),
            role_id=role.id,
            is_active=True,
            auth_method=auth_method,
        )
        session.add(user)
        await session.flush()
        user_id = user.id
        await session.commit()

    async with factory() as session:
        result: User | None = await session.get(User, user_id)
        assert result is not None
        return result


@pytest.mark.asyncio
async def test_identity_provider_protocol_exists() -> None:
    """IdentityProvider Protocol и AuthResult dataclass существуют."""
    assert IdentityProvider is not None
    assert AuthResult is not None
    # Protocol — это type hint, не класс с __init__
    assert hasattr(IdentityProvider, "__protocol_attrs__")


@pytest.mark.asyncio
async def test_local_provider_authenticates_valid_credentials(
    app_fixture: FastAPI,
) -> None:
    """LocalIdentityProvider: правильный пароль → AuthResult."""
    user = await _seed_user(app_fixture)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        provider = LocalIdentityProvider(session)
        result = await provider.authenticate(
            credentials={"email": "test@orqion.local", "password": "pass-123"}
        )
        assert result.user.id == user.id
        assert result.auth_method == "local"


@pytest.mark.asyncio
async def test_local_provider_rejects_wrong_password(app_fixture: FastAPI) -> None:
    """LocalIdentityProvider: неверный пароль → InvalidCredentials."""
    await _seed_user(app_fixture)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        provider = LocalIdentityProvider(session)
        with pytest.raises(InvalidCredentials):
            await provider.authenticate(
                credentials={"email": "test@orqion.local", "password": "wrong"}
            )


@pytest.mark.asyncio
async def test_local_provider_rejects_unknown_email(app_fixture: FastAPI) -> None:
    """LocalIdentityProvider: несуществующий email → InvalidCredentials."""
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        provider = LocalIdentityProvider(session)
        with pytest.raises(InvalidCredentials):
            await provider.authenticate(
                credentials={"email": "nobody@orqion.local", "password": "pass-123"}
            )


@pytest.mark.asyncio
async def test_local_provider_rejects_null_password_hash(app_fixture: FastAPI) -> None:
    """LocalIdentityProvider: password_hash=None (OIDC-only user) → отклоняет локальный вход."""
    await _seed_user(
        app_fixture,
        email="oidc-user@orqion.local",
        password_hash=None,
        auth_method="oidc",
    )

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        provider = LocalIdentityProvider(session)
        with pytest.raises(InvalidCredentials):
            await provider.authenticate(
                credentials={"email": "oidc-user@orqion.local", "password": "anything"}
            )


@pytest.mark.asyncio
async def test_user_model_auth_method_default_local(app_fixture: FastAPI) -> None:
    """User.auth_method defaults to "local" when not specified."""
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
            email="default-auth@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.commit()

    async with factory() as session:
        result = await session.execute(
            select(User).where(User.email == "default-auth@orqion.local")
        )
        db_user = result.scalar_one()
        assert db_user.auth_method == "local"
        assert db_user.external_subject is None
        assert db_user.external_issuer is None


@pytest.mark.asyncio
async def test_user_model_supports_oidc_fields(app_fixture: FastAPI) -> None:
    """User model: external_subject/external_issuer/auth_method="oidc" сохраняются."""
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
            email="oidc-fields@orqion.local",
            password_hash=None,
            role_id=role.id,
            is_active=True,
            auth_method="oidc",
            external_subject="oidc-subject-123",
            external_issuer="https://idp.example.com",
        )
        session.add(user)
        await session.commit()

    async with factory() as session:
        result = await session.execute(select(User).where(User.email == "oidc-fields@orqion.local"))
        db_user = result.scalar_one()
        assert db_user.auth_method == "oidc"
        assert db_user.password_hash is None
        assert db_user.external_subject == "oidc-subject-123"
        assert db_user.external_issuer == "https://idp.example.com"


@pytest.mark.asyncio
async def test_local_login_still_works_through_api(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """T-404a regression: existing /api/auth/login endpoint works after refactor."""
    await _seed_user(app_fixture, email="api-login@orqion.local")

    resp = await api_client.post(
        "/api/auth/login",
        json={"email": "api-login@orqion.local", "password": "pass-123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == "api-login@orqion.local"
    assert "capabilities" in data["user"]
