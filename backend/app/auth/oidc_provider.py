"""OIDC-провайдер идентичности (ADR-5, T-404b).

Authorization Code Flow + PKCE (S256).
ID-токен валидируется через JWKS (подпись, iss, aud, exp).
Group→role mapping — конфигурацией (ORQION_OIDC_GROUP_ROLE_MAP).
JIT-провижининг: новый пользователь создаётся при первом входе.
authlib — ленивый импорт, extras orqion[oidc], не core.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets as secrets_mod
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import write_audit
from app.auth.provider import AuthResult, IdentityProvider
from app.config import Settings
from app.crypto.service import decrypt_api_key, encrypt_api_key
from app.db.models import Role, User
from app.errors import OrqionError


class OidcError(OrqionError):
    error_code = "oidc_error"
    reason = "Ошибка аутентификации OIDC"
    status_code = 400

    def __init__(self, message: str = "", *, hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        if message:
            self.reason = message


def _generate_pkce() -> tuple[str, str]:
    """Генерирует PKCE code_verifier и code_challenge (S256)."""
    verifier = secrets_mod.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


def _generate_state() -> str:
    """Генерирует случайный state для CSRF защиты."""
    return secrets_mod.token_urlsafe(32)


def _sign_state_cookie(state: str, secret_key: str) -> str:
    """Подписывает state для cookie: state.hmac_sha256."""
    sig = hmac.new(secret_key.encode(), state.encode(), hashlib.sha256).hexdigest()
    return f"{state}.{sig}"


def _verify_state_cookie(signed: str, secret_key: str) -> str | None:
    """Проверяет подпись state из cookie. Возвращает state или None."""
    if "." not in signed:
        return None
    state, _sig = signed.rsplit(".", 1)
    expected = _sign_state_cookie(state, secret_key)
    if not hmac.compare_digest(expected, signed):
        return None
    return state


class OidcIdentityProvider(IdentityProvider):
    """OIDC-аутентификация: authorization code flow + PKCE.

    authlib импортируется лениво — не требуется для профиля minimal.
    """

    def __init__(self, session: AsyncSession, settings: Settings, workspace_id: str) -> None:
        self._session = session
        self._settings = settings
        self._workspace_id = workspace_id
        self._client_id = settings.oidc_client_id
        self._client_secret = settings.oidc_client_secret
        self._issuer = settings.oidc_issuer.rstrip("/")
        self._redirect_uri = settings.oidc_redirect_uri
        self._secret_key = settings.secret_key or "fallback"

    def _parse_group_role_map(self) -> dict[str, str]:
        """Парсит ORQION_OIDC_GROUP_ROLE_MAP (JSON-строка)."""
        try:
            raw: dict[str, object] = json.loads(self._settings.oidc_group_role_map)
            return {str(k): str(v) for k, v in raw.items()}
        except (json.JSONDecodeError, TypeError):
            return {}

    async def _fetch_discovery(self) -> dict[str, str]:
        """Получает OIDC discovery-документ."""
        discovery_url = f"{self._issuer}/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            raw: dict[str, object] = dict(resp.json())
            data: dict[str, str] = {str(k): str(v) for k, v in raw.items()}
            return data

    async def _exchange_code(
        self, code: str, code_verifier: str, discovery: dict[str, str]
    ) -> dict[str, str]:
        """Обменивает authorization code на tokens."""
        token_url = discovery["token_endpoint"]
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
            tokens: dict[str, str] = resp.json()
            return tokens

    def _validate_id_token(self, id_token: str, discovery: dict[str, str]) -> dict[str, object]:
        """Валидирует ID-токен: подпись (JWKS), iss, aud, exp.

        Использует authlib лениво.
        """
        try:
            from authlib.jose import JsonWebToken  # type: ignore[import-untyped]
            from authlib.oidc.core import IDToken  # type: ignore[import-untyped]
        except ImportError as e:
            raise OidcError(
                "authlib не установлен. Установите: pip install orqion[oidc]",
                hint="orqion[oidc]",
            ) from e

        # Получаем JWKS
        jwks_url = discovery.get("jwks_uri", "")
        if not jwks_url:
            raise OidcError("JWKS URI не найден в discovery-документе")

        # Синхронный httpx для JWKS (один вызов, малый overhead)
        with httpx.Client(timeout=10) as client:
            jwks_resp = client.get(jwks_url)
            jwks_resp.raise_for_status()
            jwks: dict[str, object] = jwks_resp.json()

        jwt = JsonWebToken(algorithms=["RS256"])
        claims_options = {
            "iss": {"value": self._issuer},
            "aud": {"value": self._client_id},
        }
        # authlib validate: проверяет exp (с 60s clock skew tolerance), iss, aud, подпись
        claims = jwt.decode(
            id_token,
            key=jwks,
            claims_cls=IDToken,
            claims_options=claims_options,
        )
        claims.validate(leeway=60)
        result: dict[str, object] = dict(claims)
        return result

    async def authenticate(self, credentials: dict[str, str]) -> AuthResult:
        """Аутентифицирует через OIDC callback.

        credentials: {"code": ..., "code_verifier": ..., "state": ...}
        """
        code = credentials.get("code", "")
        code_verifier = credentials.get("code_verifier", "")
        state = credentials.get("state", "")

        if not code or not code_verifier or not state:
            raise OidcError("Отсутствуют code, code_verifier или state")

        discovery = await self._fetch_discovery()
        tokens = await self._exchange_code(code, code_verifier, discovery)
        id_token = tokens.get("id_token", "")
        if not id_token:
            raise OidcError("ID-токен отсутствует в ответе")

        claims = self._validate_id_token(id_token, discovery)

        email = str(claims.get("email", ""))
        subject = str(claims.get("sub", ""))
        if not email:
            raise OidcError("Email не найден в ID-токене")

        # Группы из claims (зависит от IdP — может быть "groups", "roles", "realm_access")
        groups = self._extract_groups(claims)
        role_name = self._resolve_role(groups)

        user = await self._provision_user(email, subject, self._issuer, role_name)

        # Сохраняем refresh_token для фоновой синхронизации (T-405)
        refresh_token = tokens.get("refresh_token", "")
        if refresh_token and self._settings.oidc_sync_enabled:
            user.refresh_token_enc = encrypt_api_key(refresh_token, self._secret_key)

        return AuthResult(user=user, auth_method="oidc")

    def _extract_groups(self, claims: dict[str, object]) -> list[str]:
        """Извлекает группы из claims (groups, roles, realm_access.roles)."""
        groups: list[str] = []
        if "groups" in claims:
            val = claims["groups"]
            if isinstance(val, list):
                groups = [str(g) for g in val]
        if not groups and "roles" in claims:
            val = claims["roles"]
            if isinstance(val, list):
                groups = [str(g) for g in val]
        if not groups and "realm_access" in claims:
            val = claims["realm_access"]
            if isinstance(val, dict):
                roles = val.get("roles", [])
                if isinstance(roles, list):
                    groups = [str(g) for g in roles]
        return groups

    def _resolve_role(self, groups: list[str]) -> str:
        """Сопоставляет группы → роль через конфигурацию."""
        group_map = self._parse_group_role_map()
        for group in groups:
            if group in group_map:
                return group_map[group]
        return self._settings.oidc_default_role

    async def _provision_user(
        self,
        email: str,
        subject: str,
        issuer: str,
        role_name: str,
    ) -> User:
        """JIT-провижининг: находит или создаёт пользователя.

        Существующий пользователь с password_hash → auth_method="mixed".
        Новый пользователь → auth_method="oidc", password_hash=None.
        """
        workspace_id = self._workspace_id
        # Fallback: workspace_id from session info if available
        result = await self._session.execute(
            select(User).where(
                User.workspace_id == workspace_id,
                User.email == email,
            )
        )
        user = result.scalar_one_or_none()

        # Resolve role
        role_result = await self._session.execute(
            select(Role).where(
                Role.workspace_id == workspace_id,
                Role.name == role_name,
            )
        )
        role = role_result.scalar_one_or_none()
        if role is None:
            raise OidcError(
                f"Роль '{role_name}' не найдена в workspace",
                hint="Проверьте ORQION_OIDC_DEFAULT_ROLE и ORQION_OIDC_GROUP_ROLE_MAP",
            )

        if user is not None:
            # Существующий пользователь — обновляем
            old_role_id = user.role_id
            user.external_subject = subject
            user.external_issuer = issuer
            if user.password_hash is not None:
                user.auth_method = "mixed"
            else:
                user.auth_method = "oidc"

            if old_role_id != role.id:
                old_role_result = await self._session.execute(
                    select(Role).where(Role.id == old_role_id)
                )
                old_role = old_role_result.scalar_one_or_none()
                await write_audit(
                    self._session,
                    workspace_id=workspace_id,
                    actor_user_id=user.id,
                    action="user.role_changed",
                    object_type="user",
                    object_id=user.id,
                    meta={
                        "old_role": old_role.name if old_role else None,
                        "new_role": role.name,
                        "source": "oidc_group_mapping",
                    },
                )
                user.role_id = role.id
            await self._session.flush()
            return user

        # Новый пользователь (JIT)
        user = User(
            workspace_id=workspace_id,
            email=email,
            password_hash=None,
            role_id=role.id,
            is_active=True,
            auth_method="oidc",
            external_subject=subject,
            external_issuer=issuer,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    def build_authorize_url(
        self, discovery: dict[str, str], state: str, code_challenge: str
    ) -> str:
        """Строит URL для redirect к IdP authorize endpoint."""
        authorize_url = discovery["authorization_endpoint"]
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": "openid email profile groups",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{authorize_url}?{urlencode(params)}"

    async def _refresh_token_exchange(
        self, refresh_token: str, discovery: dict[str, str]
    ) -> dict[str, str]:
        """Обменивает refresh_token на новый access_token (+ возможно новый refresh_token).

        Возвращает словарь с токенами. Возбуждает httpx.HTTPStatusError
        при явном отказе IdP (400/401/invalid_grant).
        """
        token_url = discovery["token_endpoint"]
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(token_url, data=data)
            resp.raise_for_status()
            tokens: dict[str, str] = resp.json()
            return tokens

    async def _fetch_userinfo(
        self, access_token: str, discovery: dict[str, str]
    ) -> dict[str, object]:
        """Получает userinfo с IdP — группы и email.

        Возбуждает httpx.HTTPStatusError при отказе.
        """
        userinfo_url = discovery.get("userinfo_endpoint", "")
        if not userinfo_url:
            return {}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            raw: dict[str, object] = dict(resp.json())
            return raw

    async def sync_user(self, user: User) -> bool:
        """Фоновая синхронизация одного OIDC-пользователя (T-405).

        1. Расшифровать refresh_token.
        2. Exchange → новый access_token (+ возможно новый refresh_token).
        3. userinfo → группы → role update (переиспользует логику _provision_user).
        4. Сохранить новый refresh_token (ротация).

        Возвращает True если sync успешен, False если пользователь удалён/отозван.
        Сетевые ошибки пробрасываются наверх (scheduler решает пропускать).
        Явный отказ IdP (400/401) → is_active=False + audit + return False.
        """
        if not user.refresh_token_enc:
            return True  # Нет токена — нечего синхронизировать

        discovery = await self._fetch_discovery()
        refresh_token = decrypt_api_key(user.refresh_token_enc, self._secret_key)

        try:
            tokens = await self._refresh_token_exchange(refresh_token, discovery)
        except httpx.HTTPStatusError as exc:
            # Явный отказ IdP — токен отозван / пользователь удалён
            status = exc.response.status_code
            if status in (400, 401):
                user.is_active = False
                user.refresh_token_enc = None
                await write_audit(
                    self._session,
                    workspace_id=self._workspace_id,
                    actor_user_id=user.id,
                    action="user.status_changed",
                    object_type="user",
                    object_id=user.id,
                    meta={
                        "old": True,
                        "new": False,
                        "target_email": user.email,
                        "source": "oidc_sync_revoked",
                    },
                )
                await self._session.flush()
                return False
            raise  # 5xx и другие — пробрасываем как сетевую ошибку

        # Ротация: сохраняем новый refresh_token если IdP его вернул
        new_refresh_token = tokens.get("refresh_token", "")
        if new_refresh_token:
            user.refresh_token_enc = encrypt_api_key(new_refresh_token, self._secret_key)

        # userinfo → группы → role update
        access_token = tokens.get("access_token", "")
        if access_token:
            claims = await self._fetch_userinfo(access_token, discovery)
            if claims:
                groups = self._extract_groups(claims)
                role_name = self._resolve_role(groups)

                # Переиспользуем логику role resolution + audit из _provision_user
                role_result = await self._session.execute(
                    select(Role).where(
                        Role.workspace_id == self._workspace_id,
                        Role.name == role_name,
                    )
                )
                role = role_result.scalar_one_or_none()
                if role is not None and user.role_id != role.id:
                    old_role_result = await self._session.execute(
                        select(Role).where(Role.id == user.role_id)
                    )
                    old_role = old_role_result.scalar_one_or_none()
                    await write_audit(
                        self._session,
                        workspace_id=self._workspace_id,
                        actor_user_id=user.id,
                        action="user.role_changed",
                        object_type="user",
                        object_id=user.id,
                        meta={
                            "old_role": old_role.name if old_role else None,
                            "new_role": role.name,
                            "source": "oidc_sync_group_mapping",
                        },
                    )
                    user.role_id = role.id

        await self._session.flush()
        return True
