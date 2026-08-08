"""Тест шифрования AES-GCM: roundtrip, разные nonce, неверный ключ."""

from __future__ import annotations

import pytest
from app.crypto.service import decrypt_api_key, encrypt_api_key


def test_encrypt_decrypt_roundtrip() -> None:
    plaintext = "sk-abc123-xyz456"
    secret = "my-secret-key"
    encrypted = encrypt_api_key(plaintext, secret)
    assert encrypted != plaintext
    assert decrypt_api_key(encrypted, secret) == plaintext


def test_different_encryptions_have_different_nonce() -> None:
    """Каждое шифрование — новый nonce, шифрты разные."""
    plaintext = "sk-same-key"
    secret = "my-secret"
    e1 = encrypt_api_key(plaintext, secret)
    e2 = encrypt_api_key(plaintext, secret)
    assert e1 != e2
    assert decrypt_api_key(e1, secret) == plaintext
    assert decrypt_api_key(e2, secret) == plaintext


def test_decrypt_with_wrong_key_fails() -> None:
    from cryptography.exceptions import InvalidTag

    plaintext = "sk-secret"
    encrypted = encrypt_api_key(plaintext, "right-key")
    with pytest.raises(InvalidTag):
        decrypt_api_key(encrypted, "wrong-key")


def test_empty_plaintext() -> None:
    encrypted = encrypt_api_key("", "secret")
    assert decrypt_api_key(encrypted, "secret") == ""


def test_unicode_plaintext() -> None:
    plaintext = "ключ-密码-🔑"
    encrypted = encrypt_api_key(plaintext, "secret")
    assert decrypt_api_key(encrypted, "secret") == plaintext


def test_encrypted_format_is_base64() -> None:
    import base64

    encrypted = encrypt_api_key("test", "secret")
    decoded = base64.b64decode(encrypted)
    assert len(decoded) >= 24  # 12 nonce + 12+ ciphertext
