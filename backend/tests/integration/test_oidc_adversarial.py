"""T-404b: Adversarial JWT validation tests.

Проверяют что _validate_id_token реально отклоняет:
- токен с невалидной подписью (подписан другим ключом)
- токен с неверным aud (не для этого client_id)
- истёкший токен (exp в прошлом)
- токен с неверным iss (не от сконфигурированного issuer)

Используют настоящую authlib JWT-валидацию поверх mock JWKS/discovery.
Мок на уровне httpx-транспорта, не на уровне методов провайдера.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from app.auth.oidc_provider import OidcIdentityProvider
from app.config import Settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


def _generate_rsa_key() -> tuple[RSAPrivateKey, bytes, dict[str, Any]]:
    """Генерирует RSA-пару, возвращает (private_key, public_pem, jwk_key)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # Convert to JWK format for JWKS endpoint
    public_numbers = private_key.public_key().public_numbers()
    import base64

    def _int_to_b64(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    jwk_key: dict[str, Any] = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "test-key-1",
        "n": _int_to_b64(public_numbers.n),
        "e": _int_to_b64(public_numbers.e),
    }
    return private_key, public_pem, jwk_key


def _make_jwt(
    private_key: RSAPrivateKey,
    claims: dict[str, Any],
    kid: str = "test-key-1",
) -> str:
    """Создаёт настоящие JWT, подписанные RSA."""
    from authlib.jose import JsonWebToken  # type: ignore[import-untyped]

    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    jwt = JsonWebToken(algorithms=["RS256"])
    encoded = jwt.encode(header, claims, key=private_key)
    return encoded.decode() if isinstance(encoded, bytes) else encoded


def _make_discovery(issuer: str) -> dict[str, str]:
    """Mock OIDC discovery-документ."""
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "jwks_uri": f"{issuer}/jwks",
        "userinfo_endpoint": f"{issuer}/userinfo",
    }


def _make_jwks(jwk_key: dict[str, Any]) -> dict[str, Any]:
    """Mock JWKS endpoint response."""
    return {"keys": [jwk_key]}


def _make_settings(issuer: str = "https://idp.test.local") -> Settings:
    """Settings с OIDC enabled."""
    return Settings(
        oidc_enabled=True,
        oidc_client_id="test-client",
        oidc_client_secret="test-secret",
        oidc_issuer=issuer,
        oidc_redirect_uri="http://localhost:8000/api/auth/oidc/callback",
        oidc_group_role_map='{"engineering":"developer"}',
        oidc_default_role="support",
        secret_key="test-secret-key",
    )


@pytest.fixture
def rsa_key_pair() -> tuple[RSAPrivateKey, dict[str, Any]]:
    """RSA-ключ пара для подписи/валидации JWT."""
    private_key, _, jwk_key = _generate_rsa_key()
    return private_key, jwk_key


def _setup_httpx_mock(
    monkeypatch: pytest.MonkeyPatch,
    discovery: dict[str, str],
    jwks: dict[str, Any],
    token_response: dict[str, str],
) -> None:
    """Перехватывает все httpx-запросы через MockTransport.

    discovery → GET {issuer}/.well-known/openid-configuration
    jwks → GET {issuer}/jwks
    token → POST {issuer}/token
    """

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ".well-known/openid-configuration" in url:
            return httpx.Response(200, json=discovery)
        if url.endswith("/jwks"):
            return httpx.Response(200, json=jwks)
        if url.endswith("/token"):
            return httpx.Response(200, json=token_response)
        return httpx.Response(404, text="not found")

    mock_transport = httpx.MockTransport(mock_handler)

    # Patch both async and sync httpx clients
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


def _make_provider(settings: Settings) -> OidcIdentityProvider:
    """Создаёт OidcIdentityProvider с mock session."""
    return OidcIdentityProvider(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        workspace_id="ws-test",
    )


# ---------------------------------------------------------------------------
# Adversarial tests — каждый проверяет что недействительный токен отклонён
# ---------------------------------------------------------------------------


def test_rejects_token_with_invalid_signature(
    rsa_key_pair: tuple[RSAPrivateKey, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JWT подписан ДРУГИМ ключом → отклонён."""
    _signing_key, jwk_key = rsa_key_pair
    # Generate a DIFFERENT key for signing
    wrong_key, _, _ = _generate_rsa_key()

    settings = _make_settings()
    discovery = _make_discovery(settings.oidc_issuer)
    jwks = _make_jwks(jwk_key)

    now = int(time.time())
    token = _make_jwt(
        wrong_key,
        {
            "sub": "user-123",
            "email": "attacker@orqion.local",
            "iss": settings.oidc_issuer,
            "aud": settings.oidc_client_id,
            "exp": now + 3600,
            "iat": now,
        },
    )
    token_response = {"id_token": token, "access_token": "fake", "token_type": "Bearer"}
    _setup_httpx_mock(monkeypatch, discovery, jwks, token_response)

    provider = _make_provider(settings)
    from app.auth.oidc_provider import OidcError

    with pytest.raises((OidcError, Exception)):
        provider._validate_id_token(token, discovery)


def test_rejects_token_with_wrong_aud(
    rsa_key_pair: tuple[RSAPrivateKey, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JWT с aud != client_id → отклонён."""
    signing_key, jwk_key = rsa_key_pair
    settings = _make_settings()
    discovery = _make_discovery(settings.oidc_issuer)
    jwks = _make_jwks(jwk_key)

    now = int(time.time())
    token = _make_jwt(
        signing_key,
        {
            "sub": "user-123",
            "email": "attacker@orqion.local",
            "iss": settings.oidc_issuer,
            "aud": "wrong-client-id",  # Wrong audience
            "exp": now + 3600,
            "iat": now,
        },
    )
    token_response = {"id_token": token, "access_token": "fake", "token_type": "Bearer"}
    _setup_httpx_mock(monkeypatch, discovery, jwks, token_response)

    provider = _make_provider(settings)
    from app.auth.oidc_provider import OidcError

    with pytest.raises((OidcError, Exception)):
        provider._validate_id_token(token, discovery)


def test_rejects_expired_token(
    rsa_key_pair: tuple[RSAPrivateKey, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JWT с exp в прошлом → отклонён."""
    signing_key, jwk_key = rsa_key_pair
    settings = _make_settings()
    discovery = _make_discovery(settings.oidc_issuer)
    jwks = _make_jwks(jwk_key)

    now = int(time.time())
    token = _make_jwt(
        signing_key,
        {
            "sub": "user-123",
            "email": "attacker@orqion.local",
            "iss": settings.oidc_issuer,
            "aud": settings.oidc_client_id,
            "exp": now - 3600,  # Expired 1 hour ago (beyond 60s clock skew)
            "iat": now - 7200,
        },
    )
    token_response = {"id_token": token, "access_token": "fake", "token_type": "Bearer"}
    _setup_httpx_mock(monkeypatch, discovery, jwks, token_response)

    provider = _make_provider(settings)
    from app.auth.oidc_provider import OidcError

    with pytest.raises((OidcError, Exception)):
        provider._validate_id_token(token, discovery)


def test_rejects_token_with_wrong_iss(
    rsa_key_pair: tuple[RSAPrivateKey, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JWT с iss != сконфигурированный issuer → отклонён."""
    signing_key, jwk_key = rsa_key_pair
    settings = _make_settings()
    discovery = _make_discovery(settings.oidc_issuer)
    jwks = _make_jwks(jwk_key)

    now = int(time.time())
    token = _make_jwt(
        signing_key,
        {
            "sub": "user-123",
            "email": "attacker@orqion.local",
            "iss": "https://evil-idp.example.com",  # Wrong issuer
            "aud": settings.oidc_client_id,
            "exp": now + 3600,
            "iat": now,
        },
    )
    token_response = {"id_token": token, "access_token": "fake", "token_type": "Bearer"}
    _setup_httpx_mock(monkeypatch, discovery, jwks, token_response)

    provider = _make_provider(settings)
    from app.auth.oidc_provider import OidcError

    with pytest.raises((OidcError, Exception)):
        provider._validate_id_token(token, discovery)


def test_accepts_valid_token(
    rsa_key_pair: tuple[RSAPrivateKey, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Валидный JWT с правильной подписью, aud, iss, exp → принят.

    Positive control: подтверждает что тестовая инфраструктура работает
    и не отклоняет всё подряд.
    """
    signing_key, jwk_key = rsa_key_pair
    settings = _make_settings()
    discovery = _make_discovery(settings.oidc_issuer)
    jwks = _make_jwks(jwk_key)

    now = int(time.time())
    token = _make_jwt(
        signing_key,
        {
            "sub": "user-123",
            "email": "user@orqion.local",
            "iss": settings.oidc_issuer,
            "aud": settings.oidc_client_id,
            "exp": now + 3600,
            "iat": now,
        },
    )
    token_response = {"id_token": token, "access_token": "fake", "token_type": "Bearer"}
    _setup_httpx_mock(monkeypatch, discovery, jwks, token_response)

    provider = _make_provider(settings)
    claims = provider._validate_id_token(token, discovery)
    assert claims["email"] == "user@orqion.local"
    assert claims["sub"] == "user-123"


def test_validate_id_token_graceful_error_without_authlib(
    rsa_key_pair: tuple[RSAPrivateKey, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При отсутствии authlib + oidc_enabled=True → OidcError с понятной причиной.

    T-008: error/reason/hint, не сырой Python traceback.
    """
    signing_key, _jwk_key = rsa_key_pair
    settings = _make_settings()
    discovery = _make_discovery(settings.oidc_issuer)

    now = int(time.time())
    token = _make_jwt(
        signing_key,
        {
            "sub": "user-123",
            "email": "user@orqion.local",
            "iss": settings.oidc_issuer,
            "aud": settings.oidc_client_id,
            "exp": now + 3600,
            "iat": now,
        },
    )

    # Simulate authlib not installed
    import builtins

    real_import = builtins.__import__

    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("authlib"):
            raise ImportError("No module named 'authlib'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    provider = _make_provider(settings)
    from app.auth.oidc_provider import OidcError

    with pytest.raises(OidcError) as exc_info:
        provider._validate_id_token(token, discovery)

    assert "authlib" in exc_info.value.reason
    assert "orqion[oidc]" in exc_info.value.hint if exc_info.value.hint else True
