"""Фикстуры BlobStore для контрактных тестов.

T-201: LocalBlobStore.
T-202 подключит S3BlobStore тем же набором тестов, передав свою фикстуру.
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from app.rag.blob import LocalBlobStore


@pytest_asyncio.fixture
async def blob_store(tmp_path: Path) -> LocalBlobStore:
    """Экземпляр LocalBlobStore во временном каталоге."""
    root = str(tmp_path / "blobs")
    return LocalBlobStore(root)


@pytest_asyncio.fixture
async def blob_store_factory() -> object:
    """Фабрика для создания BlobStore по произвольному root.

    T-202 передаст S3BlobStore-фабрику с тем же протоколом.
    """
    def factory(root: str) -> LocalBlobStore:
        return LocalBlobStore(root)

    return factory
