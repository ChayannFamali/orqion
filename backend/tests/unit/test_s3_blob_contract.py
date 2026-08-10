"""Контрактные тесты S3BlobStore через moto_server.

Параметризованы тем же набором, что и LocalBlobStore (test_blob_contract.py).
T-202 подключает S3-реализацию, передавая свою фикстуру.

Условие запуска: aioboto3 и moto установлены (extras [s3] + dev).
Если не установлены — тесты skip, не fail.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from collections.abc import Generator

import pytest

aioboto3 = pytest.importorskip("aioboto3")
moto = pytest.importorskip("moto")

# re-export контрактных тестов из LocalBlobStore
# с параметризацией через S3-фикстуру
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


class _S3Server:
    """Запускает moto_server в отдельном процессе для тестов."""

    def __init__(self, port: int = 5001) -> None:
        self._port = port
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self._process = subprocess.Popen(
            ["python", "-m", "moto.server", "-p", str(self._port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Ждём, пока сервер поднимется
        for _ in range(30):
            try:
                with socket.create_connection(("127.0.0.1", self._port), timeout=1):
                    return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError("moto_server did not start")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.wait()
            self._process = None

    @property
    def endpoint_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"


@pytest.fixture(scope="module")
def s3_server() -> Generator[_S3Server, None, None]:
    """Запускает moto_server на фиксированном порту."""
    server = _S3Server()
    server.start()
    yield server
    server.stop()


@pytest.fixture
async def blob_store(s3_server: _S3Server, monkeypatch: pytest.MonkeyPatch) -> S3BlobStore:  # type: ignore[name-defined]  # noqa: F821
    """Экземпляр S3BlobStore с moto_server."""
    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

    from app.rag.s3 import S3BlobStore

    store = S3BlobStore(
        endpoint_url=s3_server.endpoint_url,
        bucket="test-blobs",
        access_key="test",
        secret_key="test",
        region="us-east-1",
    )
    return store


@pytest.fixture
async def blob_store_factory(s3_server: _S3Server) -> object:
    """Фабрика S3BlobStore — для теста directory_auto_created."""
    os.environ["AWS_ACCESS_KEY_ID"] = "test"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "test"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

    def factory(root: str) -> object:
        from app.rag.s3 import S3BlobStore

        return S3BlobStore(
            endpoint_url=s3_server.endpoint_url,
            bucket=f"test-blobs-dir-{uuid.uuid4().hex[:8]}",
            access_key="test",
            secret_key="test",
            region="us-east-1",
        )

    return factory
