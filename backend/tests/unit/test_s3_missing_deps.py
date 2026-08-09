"""Тест S3BlobStore: отказ при отсутствии aioboto3.

Если aioboto3 установлен — skip, т.к. ConfigurationError не вызывается.
"""

from __future__ import annotations

import pytest

try:
    import aioboto3  # type: ignore[import-not-found]  # noqa: F401

    pytest.skip("aioboto3 is installed, skipping missing-deps test", allow_module_level=True)
except ImportError:
    pass

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
