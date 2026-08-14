"""T-405: OIDC sync — фоновая синхронизация групп и отзыв доступа.

Тестируют sync_user через mock httpx MockTransport:
- refresh → новый access_token → userinfo → role update
- ротация refresh_token (новый токен перезаписывает старый)
- invalid_grant (400) → is_active=False + audit
- 401 → is_active=False + audit
- сетевая ошибка (ConnectError) → пробрасывается, is_active не меняется
- 5xx → пробрасывается, is_active не меняется
- refresh_token сохраняется при логине когда oidc_sync_enabled
- refresh_token НЕ сохраняется когда oidc_sync_enabled=False

Мок на уровне httpx-транспорта, не на уровне методов провайдера.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.auth.bootstrap import ensure_builtin_roles
from app.auth.oidc_provider import OidcIdentityProvider
from app.config import Settings
from app.crypto.service import decrypt_api_key, encrypt_api_key
from app.db.models import AuditLog, Role, User
from fastapi import FastAPI
from sqlalchemy import select


def _make_oidc_settings(**overrides: Any) -> Settings:
    """Settings с OIDC + sync enabled."""
    defaults: dict[str, Any] = {
        "oidc_enabled": True,
        "oidc_client_id": "test-client",
        "oidc_client_secret": "test-secret",
        "oidc_issuer": "https://idp.test.local",
        "oidc_redirect_uri": "http://localhost:8000/api/auth/oidc/callback",
        "oidc_group_role_map": json.dumps({"engineering": "developer", "admins": "admin"}),
        "oidc_default_role": "support",
        "oidc_sync_enabled": True,
        "oidc_sync_interval_seconds": 300,
        "secret_key": "test-secret-key-for-hmac",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_discovery(issuer: str = "https://idp.test.local") -> dict[str, str]:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
        "userinfo_endpoint": f"{issuer}/userinfo",
    }


def _setup_httpx_mock(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    """Перехватывает все httpx-запросы через MockTransport с кастомным handler."""

    mock_transport = httpx.MockTransport(handler)

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = mock_transport
            super().__init__(**kwargs)

    class PatchedClient(httpx.Client):
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = mock_transport
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)
    monkeypatch.setattr(httpx, "Client", PatchedClient)


async def _seed_workspace_and_roles(app_fixture: FastAPI) -> str:
    """Создаёт workspace и встроенные роли."""
    factory = app_fixture.state.db_session_factory
    workspace_id: str = app_fixture.state.workspace_id
    async with factory() as session:
        await ensure_builtin_roles(session, workspace_id)
        await session.commit()
    return workspace_id


async def _create_oidc_user(
    app_fixture: FastAPI,
    workspace_id: str,
    email: str = "sync@orqion.local",
    role_name: str = "developer",
    refresh_token: str = "initial-refresh-token",
    secret_key: str = "test-secret-key-for-hmac",
) -> str:
    """Создаёт OIDC-пользователя с зашифрованным refresh_token. Возвращает user.id."""
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        role_result = await session.execute(
            select(Role).where(Role.name == role_name, Role.workspace_id == workspace_id)
        )
        role = role_result.scalar_one()
        user = User(
            workspace_id=workspace_id,
            email=email,
            password_hash=None,
            role_id=role.id,
            is_active=True,
            auth_method="oidc",
            external_subject="oidc-sub-test",
            external_issuer="https://idp.test.local",
            refresh_token_enc=encrypt_api_key(refresh_token, secret_key),
        )
        session.add(user)
        await session.commit()
        return user.id


# ---------------------------------------------------------------------------
# sync_user tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_refresh_success_updates_role(
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_user: refresh успешен → userinfo → группы изменились → роль обновлена."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    secret_key = app_fixture.state.secret_key
    user_id = await _create_oidc_user(
        app_fixture, workspace_id, role_name="developer", secret_key=secret_key
    )

    # userinfo возвращает группу "admins" → роль должна смениться на "admin"
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ".well-known/openid-configuration" in url:
            return httpx.Response(200, json=_make_discovery())
        if url.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access-token",
                    "token_type": "Bearer",
                    "refresh_token": "rotated-refresh-token",
                },
            )
        if url.endswith("/userinfo"):
            return httpx.Response(
                200,
                json={"sub": "oidc-sub-test", "email": "sync@orqion.local", "groups": ["admins"]},
            )
        return httpx.Response(404, text="not found")

    _setup_httpx_mock(monkeypatch, handler)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        provider = OidcIdentityProvider(
            session=session, settings=settings, workspace_id=workspace_id
        )
        provider._secret_key = secret_key

        ok = await provider.sync_user(user)
        assert ok is True
        await session.commit()

    # Проверяем: роль изменилась на admin
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        role_result = await session.execute(select(Role).where(Role.id == user.role_id))
        role = role_result.scalar_one()
        assert role.name == "admin"
        assert user.is_active is True
        # refresh_token ротирован
        assert user.refresh_token_enc is not None
        assert decrypt_api_key(user.refresh_token_enc, secret_key) == "rotated-refresh-token"


@pytest.mark.asyncio
async def test_sync_refresh_token_rotation_saved(
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_user: IdP возвращает новый refresh_token → перезаписывает старый."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    secret_key = app_fixture.state.secret_key
    user_id = await _create_oidc_user(
        app_fixture,
        workspace_id,
        refresh_token="original-token",
        secret_key=secret_key,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ".well-known/openid-configuration" in url:
            return httpx.Response(200, json=_make_discovery())
        if url.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "token_type": "Bearer",
                    "refresh_token": "brand-new-rotated-token",
                },
            )
        if url.endswith("/userinfo"):
            return httpx.Response(
                200,
                json={
                    "sub": "oidc-sub-test",
                    "email": "sync@orqion.local",
                    "groups": ["engineering"],
                },
            )
        return httpx.Response(404)

    _setup_httpx_mock(monkeypatch, handler)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        provider = OidcIdentityProvider(
            session=session, settings=settings, workspace_id=workspace_id
        )
        provider._secret_key = secret_key

        await provider.sync_user(user)
        await session.commit()

    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert decrypt_api_key(user.refresh_token_enc, secret_key) == "brand-new-rotated-token"


@pytest.mark.asyncio
async def test_sync_no_rotation_keeps_original_token(
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_user: IdP не возвращает новый refresh_token → старый сохранён."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    secret_key = app_fixture.state.secret_key
    user_id = await _create_oidc_user(
        app_fixture,
        workspace_id,
        refresh_token="keep-me",
        secret_key=secret_key,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ".well-known/openid-configuration" in url:
            return httpx.Response(200, json=_make_discovery())
        if url.endswith("/token"):
            # Нет refresh_token в ответе
            return httpx.Response(
                200,
                json={"access_token": "new-access", "token_type": "Bearer"},
            )
        if url.endswith("/userinfo"):
            return httpx.Response(
                200,
                json={
                    "sub": "oidc-sub-test",
                    "email": "sync@orqion.local",
                    "groups": ["engineering"],
                },
            )
        return httpx.Response(404)

    _setup_httpx_mock(monkeypatch, handler)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        provider = OidcIdentityProvider(
            session=session, settings=settings, workspace_id=workspace_id
        )
        provider._secret_key = secret_key

        await provider.sync_user(user)
        await session.commit()

    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert decrypt_api_key(user.refresh_token_enc, secret_key) == "keep-me"


@pytest.mark.asyncio
async def test_sync_invalid_grant_deactivates_user(
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_user: IdP возвращает 400 invalid_grant → is_active=False + audit."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    secret_key = app_fixture.state.secret_key
    user_id = await _create_oidc_user(app_fixture, workspace_id, secret_key=secret_key)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ".well-known/openid-configuration" in url:
            return httpx.Response(200, json=_make_discovery())
        if url.endswith("/token"):
            return httpx.Response(
                400,
                json={"error": "invalid_grant", "error_description": "Token revoked"},
            )
        return httpx.Response(404)

    _setup_httpx_mock(monkeypatch, handler)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.is_active is True
        provider = OidcIdentityProvider(
            session=session, settings=settings, workspace_id=workspace_id
        )
        provider._secret_key = secret_key

        ok = await provider.sync_user(user)
        assert ok is False
        await session.commit()

    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.is_active is False
        assert user.refresh_token_enc is None

        # Audit log
        audit_result = await session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "user.status_changed",
                AuditLog.object_id == user_id,
            )
        )
        audit = audit_result.scalar_one_or_none()
        assert audit is not None
        assert audit.meta["old"] is True
        assert audit.meta["new"] is False
        assert audit.meta["source"] == "oidc_sync_revoked"


@pytest.mark.asyncio
async def test_sync_401_deactivates_user(
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_user: IdP возвращает 401 → is_active=False + audit."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    secret_key = app_fixture.state.secret_key
    user_id = await _create_oidc_user(app_fixture, workspace_id, secret_key=secret_key)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ".well-known/openid-configuration" in url:
            return httpx.Response(200, json=_make_discovery())
        if url.endswith("/token"):
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(404)

    _setup_httpx_mock(monkeypatch, handler)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        provider = OidcIdentityProvider(
            session=session, settings=settings, workspace_id=workspace_id
        )
        provider._secret_key = secret_key

        ok = await provider.sync_user(user)
        assert ok is False
        await session.commit()

    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.is_active is False


@pytest.mark.asyncio
async def test_sync_5xx_does_not_deactivate(
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_user: IdP возвращает 503 → сетевая ошибка, is_active не меняется."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    secret_key = app_fixture.state.secret_key
    user_id = await _create_oidc_user(app_fixture, workspace_id, secret_key=secret_key)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ".well-known/openid-configuration" in url:
            return httpx.Response(200, json=_make_discovery())
        if url.endswith("/token"):
            return httpx.Response(503, json={"error": "server_error"})
        return httpx.Response(404)

    _setup_httpx_mock(monkeypatch, handler)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        provider = OidcIdentityProvider(
            session=session, settings=settings, workspace_id=workspace_id
        )
        provider._secret_key = secret_key

        # 5xx пробрасывается как HTTPStatusError
        with pytest.raises(httpx.HTTPStatusError):
            await provider.sync_user(user)

    # is_active не изменился
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.is_active is True


@pytest.mark.asyncio
async def test_sync_connect_error_does_not_deactivate(
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_user: ConnectError → сетевая ошибка, is_active не меняется."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    secret_key = app_fixture.state.secret_key
    user_id = await _create_oidc_user(app_fixture, workspace_id, secret_key=secret_key)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    _setup_httpx_mock(monkeypatch, handler)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        provider = OidcIdentityProvider(
            session=session, settings=settings, workspace_id=workspace_id
        )
        provider._secret_key = secret_key

        with pytest.raises(httpx.ConnectError):
            await provider.sync_user(user)

    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.is_active is True


@pytest.mark.asyncio
async def test_sync_role_change_writes_audit(
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync_user: смена роли через userinfo → audit_log user.role_changed (source: oidc_sync)."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    secret_key = app_fixture.state.secret_key
    user_id = await _create_oidc_user(
        app_fixture, workspace_id, role_name="developer", secret_key=secret_key
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ".well-known/openid-configuration" in url:
            return httpx.Response(200, json=_make_discovery())
        if url.endswith("/token"):
            return httpx.Response(
                200,
                json={"access_token": "new-access", "token_type": "Bearer"},
            )
        if url.endswith("/userinfo"):
            return httpx.Response(
                200,
                json={"sub": "oidc-sub-test", "email": "sync@orqion.local", "groups": ["admins"]},
            )
        return httpx.Response(404)

    _setup_httpx_mock(monkeypatch, handler)

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        provider = OidcIdentityProvider(
            session=session, settings=settings, workspace_id=workspace_id
        )
        provider._secret_key = secret_key

        await provider.sync_user(user)
        await session.commit()

    async with factory() as session:
        audit_result = await session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "user.role_changed",
                AuditLog.object_id == user_id,
            )
        )
        audit = audit_result.scalar_one_or_none()
        assert audit is not None
        assert audit.meta["source"] == "oidc_sync_group_mapping"
        assert audit.meta["old_role"] == "developer"
        assert audit.meta["new_role"] == "admin"


@pytest.mark.asyncio
async def test_sync_no_refresh_token_returns_true(
    app_fixture: FastAPI,
) -> None:
    """sync_user: нет refresh_token_enc → nothing to sync, returns True."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    factory = app_fixture.state.db_session_factory

    async with factory() as session:
        role_result = await session.execute(
            select(Role).where(Role.name == "developer", Role.workspace_id == workspace_id)
        )
        role = role_result.scalar_one()
        user = User(
            workspace_id=workspace_id,
            email="no-token@orqion.local",
            password_hash=None,
            role_id=role.id,
            is_active=True,
            auth_method="oidc",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        assert user.refresh_token_enc is None
        provider = OidcIdentityProvider(
            session=session, settings=settings, workspace_id=workspace_id
        )
        ok = await provider.sync_user(user)
        assert ok is True


@pytest.mark.asyncio
async def test_deactivated_user_blocked_on_next_request(
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Приёмка T-405: deactivated user → 401 на следующем запросе.

    Сценарий: sync деактивировал пользователя (invalid_grant).
    Сессия пользователя всё ещё валидна (expires_at в будущем),
    но get_user_by_session проверяет is_active → 401.
    """
    from app.auth.sessions import create_session, get_user_by_session

    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()
    secret_key = app_fixture.state.secret_key
    factory = app_fixture.state.db_session_factory

    # Создаём OIDC-пользователя
    async with factory() as session:
        role_result = await session.execute(
            select(Role).where(Role.name == "developer", Role.workspace_id == workspace_id)
        )
        role = role_result.scalar_one()
        user = User(
            workspace_id=workspace_id,
            email="deactivated@orqion.local",
            password_hash=None,
            role_id=role.id,
            is_active=True,
            auth_method="oidc",
            refresh_token_enc=encrypt_api_key("some-token", secret_key),
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    # Создаём сессию для пользователя
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        session_id = await create_session(session, user.id, workspace_id, settings)
        await session.commit()

    # Деактивируем пользователя (имитация sync)
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.is_active = False
        await session.commit()

    # Проверяем: get_user_by_session возвращает None несмотря на валидную сессию
    async with factory() as session:
        result = await get_user_by_session(session, session_id)
        assert result is None
