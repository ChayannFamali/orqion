"""Пользователи, пароли (argon2id), сессии, зависимость current_user."""

from app.auth.bootstrap import ensure_initial_admin
from app.auth.dependencies import current_user
from app.auth.impersonate import impersonate
from app.auth.local_provider import LocalIdentityProvider
from app.auth.passwords import hash_password, verify_password
from app.auth.provider import AuthResult, IdentityProvider
from app.auth.sessions import COOKIE_NAME, create_session, invalidate_session

__all__ = [
    "COOKIE_NAME",
    "AuthResult",
    "IdentityProvider",
    "LocalIdentityProvider",
    "create_session",
    "current_user",
    "ensure_initial_admin",
    "hash_password",
    "impersonate",
    "invalidate_session",
    "verify_password",
]
