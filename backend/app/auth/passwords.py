"""Хеширование паролей argon2id + генерация случайных паролей."""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerifyMismatchError

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def generate_random_password() -> str:
    """Генерирует случайный пароль (~128 бит энтропии, ~22 символа ASCII)."""
    return secrets.token_urlsafe(16)


def hash_password(password: str) -> str:
    """Возвращает argon2id-хеш пароля."""
    hashed: str = _hasher.hash(password)
    return hashed


def verify_password(password_hash: str, password: str) -> bool:
    """Проверяет пароль против хеша. True при совпадении, False при любых расхождениях."""
    try:
        return bool(_hasher.verify(password_hash, password))
    except (VerifyMismatchError, Argon2Error):
        return False
