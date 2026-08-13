"""T-404b: OIDC provider tests.

Unit tests (без authlib):
- PKCE generation (S256)
- State cookie signing + verification
- Group→role mapping (включая default role "support")
- JIT provisioning (новый пользователь, existing user, mixed auth_method)
- OIDC disabled by default

Integration tests (требуют authlib, skipif):
- Full OIDC callback flow через mock httpx transport
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.auth.bootstrap import ensure_builtin_roles
from app.auth.oidc_provider import (
    _generate_pkce,
    _generate_state,
    _sign_state_cookie,
    _verify_state_cookie,
)
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.models import AuditLog, Role, User
from fastapi import FastAPI
from sqlalchemy import select


def _make_oidc_settings(**overrides: Any) -> Settings:
    """Settings с OIDC enabled и test-конфигурацией."""
    defaults: dict[str, Any] = {
        "oidc_enabled": True,
        "oidc_client_id": "test-client",
        "oidc_client_secret": "test-secret",
        "oidc_issuer": "https://idp.test.local",
        "oidc_redirect_uri": "http://localhost:8000/api/auth/oidc/callback",
        "oidc_group_role_map": json.dumps({"engineering": "developer", "admins": "admin"}),
        "oidc_default_role": "support",
        "secret_key": "test-secret-key-for-hmac",
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# Unit tests (не требуют authlib)
# ---------------------------------------------------------------------------


def test_pkce_generates_verifier_and_challenge() -> None:
    """PKCE: verifier и challenge генерируются, challenge — S256 base64url."""
    verifier, challenge = _generate_pkce()
    assert len(verifier) >= 43
    assert len(challenge) >= 43
    assert verifier != challenge

    # Verify S256 manually
    import base64
    import hashlib

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    assert challenge == expected


def test_state_generation_is_random() -> None:
    """State — случайная строка, разные вызовы дают разные значения."""
    s1 = _generate_state()
    s2 = _generate_state()
    assert s1 != s2
    assert len(s1) >= 32


def test_state_cookie_signing_and_verification() -> None:
    """State cookie: подпись создаётся и проверяется."""
    secret = "test-secret"
    state = "test-state-value"
    signed = _sign_state_cookie(state, secret)
    assert signed != state
    assert signed.startswith(state + ".")

    # Valid
    verified = _verify_state_cookie(signed, secret)
    assert verified == state

    # Wrong secret
    assert _verify_state_cookie(signed, "wrong-secret") is None

    # Tampered
    tampered = signed[:-1] + "x"
    assert _verify_state_cookie(tampered, secret) is None

    # No dot
    assert _verify_state_cookie("nodothere", secret) is None


def test_group_role_map_parsing() -> None:
    """Group→role map парсится из JSON-строки."""
    from app.auth.oidc_provider import OidcIdentityProvider

    settings = _make_oidc_settings()
    provider = OidcIdentityProvider(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        workspace_id="ws-test",
    )
    group_map = provider._parse_group_role_map()
    assert group_map == {"engineering": "developer", "admins": "admin"}


def test_group_role_map_invalid_json_returns_empty() -> None:
    """Невалидный JSON в group_role_map → пустой dict."""
    from app.auth.oidc_provider import OidcIdentityProvider

    settings = _make_oidc_settings(oidc_group_role_map="{invalid json}")
    provider = OidcIdentityProvider(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        workspace_id="ws-test",
    )
    assert provider._parse_group_role_map() == {}


def test_resolve_role_with_matching_group() -> None:
    """Группа в mapping → соответствующая роль."""
    from app.auth.oidc_provider import OidcIdentityProvider

    settings = _make_oidc_settings()
    provider = OidcIdentityProvider(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        workspace_id="ws-test",
    )
    assert provider._resolve_role(["engineering"]) == "developer"
    assert provider._resolve_role(["admins"]) == "admin"


def test_resolve_role_unmapped_group_defaults_to_support() -> None:
    """Немаппированная группа → default role 'support' (deny-by-default)."""
    from app.auth.oidc_provider import OidcIdentityProvider

    settings = _make_oidc_settings()
    provider = OidcIdentityProvider(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        workspace_id="ws-test",
    )
    assert provider._resolve_role(["unknown-group"]) == "support"
    assert provider._resolve_role([]) == "support"


def test_extract_groups_from_claims() -> None:
    """Группы извлекаются из разных claim-форматов (groups, roles, realm_access)."""
    from app.auth.oidc_provider import OidcIdentityProvider

    settings = _make_oidc_settings()
    provider = OidcIdentityProvider(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        workspace_id="ws-test",
    )

    # groups claim
    assert provider._extract_groups({"groups": ["engineering", "admins"]}) == [
        "engineering",
        "admins",
    ]

    # roles claim (no groups)
    assert provider._extract_groups({"roles": ["engineering"]}) == ["engineering"]

    # realm_access.roles (Keycloak-style)
    assert provider._extract_groups({"realm_access": {"roles": ["engineering"]}}) == ["engineering"]

    # No groups
    assert provider._extract_groups({}) == []


@pytest.mark.asyncio
async def test_oidc_disabled_by_default() -> None:
    """Settings: oidc_enabled=False по умолчанию."""
    settings = Settings()
    assert settings.oidc_enabled is False
    assert settings.oidc_default_role == "support"


# ---------------------------------------------------------------------------
# JIT provisioning tests (через DB, без OIDC flow)
# ---------------------------------------------------------------------------


async def _seed_workspace_and_roles(app_fixture: FastAPI) -> str:
    """Создаёт workspace + builtin roles, возвращает workspace_id."""
    factory = app_fixture.state.db_session_factory
    workspace_id: str = app_fixture.state.workspace_id
    async with factory() as session:
        await ensure_builtin_roles(session, workspace_id)
        await session.commit()
    return workspace_id


@pytest.mark.asyncio
async def test_jit_provisioning_creates_new_user(app_fixture: FastAPI) -> None:
    """JIT: новый OIDC-пользователь создаётся с auth_method="oidc"."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()

    from app.auth.oidc_provider import OidcIdentityProvider

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        provider = OidcIdentityProvider(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
        )
        user = await provider._provision_user(
            email="new-oidc@orqion.local",
            subject="oidc-sub-123",
            issuer="https://idp.test.local",
            role_name="developer",
        )
        await session.commit()
        assert user.email == "new-oidc@orqion.local"
        assert user.auth_method == "oidc"
        assert user.password_hash is None
        assert user.external_subject == "oidc-sub-123"
        assert user.external_issuer == "https://idp.test.local"


@pytest.mark.asyncio
async def test_jit_provisioning_existing_user_becomes_mixed(app_fixture: FastAPI) -> None:
    """JIT: существующий пользователь с password_hash → auth_method="mixed"."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()

    factory = app_fixture.state.db_session_factory
    # Create existing local user
    async with factory() as session:
        role_result = await session.execute(
            select(Role).where(Role.name == "developer", Role.workspace_id == workspace_id)
        )
        role = role_result.scalar_one()
        user = User(
            workspace_id=workspace_id,
            email="existing@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.commit()

    from app.auth.oidc_provider import OidcIdentityProvider

    async with factory() as session:
        provider = OidcIdentityProvider(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
        )
        user = await provider._provision_user(
            email="existing@orqion.local",
            subject="oidc-sub-456",
            issuer="https://idp.test.local",
            role_name="developer",
        )
        await session.commit()
        assert user.auth_method == "mixed"
        assert user.password_hash is not None  # Password preserved
        assert user.external_subject == "oidc-sub-456"


@pytest.mark.asyncio
async def test_jit_provisioning_role_change_writes_audit(app_fixture: FastAPI) -> None:
    """JIT: автоматическая смена роли → audit_log user.role_changed."""
    workspace_id = await _seed_workspace_and_roles(app_fixture)
    settings = _make_oidc_settings()

    factory = app_fixture.state.db_session_factory
    # Create user with "developer" role
    async with factory() as session:
        role_result = await session.execute(
            select(Role).where(Role.name == "developer", Role.workspace_id == workspace_id)
        )
        dev_role = role_result.scalar_one()
        user = User(
            workspace_id=workspace_id,
            email="role-change@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=dev_role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    from app.auth.oidc_provider import OidcIdentityProvider

    async with factory() as session:
        provider = OidcIdentityProvider(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
        )
        # Provision with different role (admin)
        await provider._provision_user(
            email="role-change@orqion.local",
            subject="oidc-sub-789",
            issuer="https://idp.test.local",
            role_name="admin",
        )
        await session.commit()

    # Verify audit log
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "user.role_changed",
                AuditLog.object_id == user_id,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.meta["old_role"] == "developer"
        assert audit.meta["new_role"] == "admin"
        assert audit.meta["source"] == "oidc_group_mapping"


@pytest.mark.asyncio
async def test_oidc_login_disabled_returns_error(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/auth/oidc/login при oidc_enabled=False → 400."""
    resp = await api_client.get("/api/auth/oidc/login")
    assert resp.status_code == 400
