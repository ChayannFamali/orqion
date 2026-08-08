"""AES-GCM шифрование API-ключей провайдеров.

Ключ выводится из secret_key через PBKDF2-HMAC-SHA256 (480000 итераций).
Nonce генерируется случайно для каждого шифрования (96 бит).
Формат хранения: base64(nonce || ciphertext).
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_SALT = b"orqion-provider-key-v1"
_KDF_ITERATIONS = 480_000
_KEY_LENGTH = 32
_NONCE_LENGTH = 12


def _derive_key(secret_key: str) -> bytes:
    """Выводит 256-битный ключ из secret_key через PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=_SALT,
        iterations=_KDF_ITERATIONS,
    )
    return kdf.derive(secret_key.encode("utf-8"))


def encrypt_api_key(plaintext: str, secret_key: str) -> str:
    """Шифрует API-ключ. Возвращает base64(nonce || ciphertext)."""
    key = _derive_key(secret_key)
    nonce = os.urandom(_NONCE_LENGTH)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_api_key(encrypted: str, secret_key: str) -> str:
    """Расшифровывает API-ключ. Возбуждает ValueError при неверном ключе."""
    key = _derive_key(secret_key)
    raw = base64.b64decode(encrypted)
    nonce = raw[:_NONCE_LENGTH]
    ciphertext = raw[_NONCE_LENGTH:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")
