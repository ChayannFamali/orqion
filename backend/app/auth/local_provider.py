"""Локальный провайдер идентичности (ADR-5).

Оборачивает существующую логику argon2 + DB-запрос (T-103)
в интерфейс IdentityProvider.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import verify_password
from app.auth.provider import AuthResult, IdentityProvider
from app.db.models import User
from app.errors import OrqionError


class InvalidCredentials(OrqionError):
    error_code = "invalid_credentials"
    reason = "Неверный email или пароль"
    status_code = 401


class LocalIdentityProvider(IdentityProvider):
    """Локальная аутентификация: email + пароль (argon2id)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def authenticate(self, credentials: dict[str, str]) -> AuthResult:
        """Аутентифицирует по email + password.

        credentials: {"email": ..., "password": ...}
        """
        email = credentials.get("email", "")
        password = credentials.get("password", "")

        result = await self._session.execute(
            select(User).where(User.email == email, User.is_active.is_(True))
        )
        user = result.scalar_one_or_none()
        if (
            user is None
            or user.password_hash is None
            or not verify_password(user.password_hash, password)
        ):
            raise InvalidCredentials()

        return AuthResult(user=user, auth_method="local")
