"""IdentityProvider: интерфейс аутентификации (ADR-5).

Две реализации: локальная (T-103) и OIDC (T-404b).
Выбор — конфигурацией (settings.oidc_enabled).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.db.models import User


@dataclass(frozen=True)
class AuthResult:
    """Результат аутентификации: пользователь + метод входа."""

    user: User
    auth_method: str  # "local" | "oidc"


class IdentityProvider(Protocol):
    """Интерфейс провайдера идентичности (ADR-5).

    LocalIdentityProvider — существующая логика argon2 + DB-сессия.
    OidcIdentityProvider — OIDC authorization code flow (T-404b).
    """

    async def authenticate(self, credentials: dict[str, str]) -> AuthResult:
        """Аутентифицирует пользователя по credentials.

        Raises:
            InvalidCredentials: неверные учётные данные.
        """
        ...
