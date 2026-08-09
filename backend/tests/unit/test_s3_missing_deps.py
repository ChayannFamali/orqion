"""Тест S3BlobStore: отказ при отсутствии aioboto3.

Этот тест не требует установки aioboto3 — проверяет, что при
отсутствии extras [s3] ошибка человекочитаемая, не ModuleNotFoundError
со стектрейсом.
"""

from __future__ import annotations

import pytest
from app.errors import ConfigurationError
from app.rag.s3 import S3BlobStore


def test_s3_missing_aioboto3_raises_configuration_error() -> None:
    """При отсутствии aioboto3 — ConfigurationError с человекочитаемым сообщением."""
    with pytest.raises(ConfigurationError) as exc_info:
        S3BlobStore(
            endpoint_url="http://localhost:9000",
            bucket="test-bucket",
            access_key="test",
            secret_key="test",
        )

    msg = str(exc_info.value)
    assert "aioboto3" in msg.lower()
    assert "pip install orqion[s3]" in (exc_info.value.hint or "")
