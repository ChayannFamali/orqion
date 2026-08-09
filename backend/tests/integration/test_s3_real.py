"""Контрактные тесты S3BlobStore против реального S3-совместимого сервера.

Запуск: с env vars ORQION_S3_ENDPOINT_URL, ORQION_S3_BUCKET, etc.
Если переменные не заданы — skip.
Бакет создаётся явно (SeaweedFS не создаёт автоматически).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytest.importorskip("aioboto3")

_s3_endpoint = os.environ.get("ORQION_S3_ENDPOINT_URL")
_s3_bucket = os.environ.get("ORQION_S3_BUCKET")

skip_reason = "Set ORQION_S3_ENDPOINT_URL and ORQION_S3_BUCKET to run real S3 tests"

# re-export контрактных тестов
from tests.unit.test_blob_contract import (  # noqa: F401
    test_delete,
    test_directory_auto_created,
    test_exists,
    test_get_reads_back_content,
    test_get_returns_multiple_chunks,
    test_interrupted_put_leaves_no_blob,
    test_put_deduplication,
    test_put_different_content_different_uri,
    test_put_empty_content,
    test_put_returns_sha256_uri,
)


@pytest.fixture(scope="module")
def _bucket_name() -> str:
    """Уникальный бакет для тестового прогона."""
    return f"{_s3_bucket}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def _create_bucket(_bucket_name: str) -> None:
    """Создаёт бакет на реальном сервере перед прогоном."""
    import aioboto3

    async def _create() -> None:
        session = aioboto3.Session(
            aws_access_key_id=os.environ.get("ORQION_S3_ACCESS_KEY", "dummy"),
            aws_secret_access_key=os.environ.get("ORQION_S3_SECRET_KEY", "dummy"),
            region_name=os.environ.get("ORQION_S3_REGION", "us-east-1"),
        )
        async with session.client("s3", endpoint_url=_s3_endpoint) as s3:
            try:
                await s3.head_bucket(Bucket=_bucket_name)
            except Exception:  # noqa: BLE001
                await s3.create_bucket(Bucket=_bucket_name)

    asyncio.run(_create())


@pytest.fixture
async def blob_store(_create_bucket: None, _bucket_name: str) -> object:
    """Экземпляр S3BlobStore против реального сервера."""
    from app.rag.s3 import S3BlobStore

    return S3BlobStore(
        endpoint_url=_s3_endpoint,
        bucket=_bucket_name,
        access_key=os.environ.get("ORQION_S3_ACCESS_KEY", "dummy"),
        secret_key=os.environ.get("ORQION_S3_SECRET_KEY", "dummy"),
        region=os.environ.get("ORQION_S3_REGION", "us-east-1"),
    )


@pytest.fixture
async def blob_store_factory(_create_bucket: None, _bucket_name: str) -> object:
    """Фабрика S3BlobStore для теста directory_auto_created."""

    def factory(root: str) -> object:
        from app.rag.s3 import S3BlobStore

        return S3BlobStore(
            endpoint_url=_s3_endpoint,
            bucket=f"{_bucket_name}-dir-{uuid.uuid4().hex[:8]}",
            access_key=os.environ.get("ORQION_S3_ACCESS_KEY", "dummy"),
            secret_key=os.environ.get("ORQION_S3_SECRET_KEY", "dummy"),
            region=os.environ.get("ORQION_S3_REGION", "us-east-1"),
        )

    return factory


# Skip весь модуль, если env vars не заданы
if not _s3_endpoint or not _s3_bucket:
    pytest.skip(skip_reason, allow_module_level=True)
