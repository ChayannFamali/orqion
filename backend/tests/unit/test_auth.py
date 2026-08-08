"""Тест argon2id: hash/verify roundtrip, разные соли, неверный пароль."""

from __future__ import annotations

from app.auth.passwords import hash_password, verify_password


def test_hash_and_verify_roundtrip() -> None:
    password = "correct horse battery staple"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(hashed, password) is True


def test_different_hashes_for_same_password() -> None:
    """argon2id использует случайную соль — два хеша одного пароля различны."""
    password = "hello-world-123"
    h1 = hash_password(password)
    h2 = hash_password(password)
    assert h1 != h2
    assert verify_password(h1, password) is True
    assert verify_password(h2, password) is True


def test_wrong_password_fails() -> None:
    hashed = hash_password("right-password")
    assert verify_password(hashed, "wrong-password") is False


def test_empty_password_fails() -> None:
    hashed = hash_password("nonempty")
    assert verify_password(hashed, "") is False


def test_hash_contains_argon2id_prefix() -> None:
    """Хеш должен содержать $argon2id$ — подтверждение алгоритма."""
    hashed = hash_password("test")
    assert "$argon2id$" in hashed
