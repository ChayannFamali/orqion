"""S3-совместимая реализация BlobStore (T-202).

Бэкенд — любой S3-совместимый сервис (MinIO, AWS S3, Yandex Object Storage).
endpoint_url задаётся в конфигурации, не в коде.
Ни одного if backend == ... — выбор бэкенда через конфигурацию, не код.

aioboto3 — extras, не core. Ленивый импорт: профиль minimal не тянет зависимость.
При отсутствии aioboto3 — ConfigurationError с человекочитаемым сообщением.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

from app.errors import ConfigurationError
from app.rag.blob import CHUNK_SIZE, BlobRef


class S3BlobStore:
    """S3-совместимая реализация BlobStore.

    Ключ — sha256 (голый hex, как в LocalBlobStore).
    Объект S3: {bucket}/{ab}/{cd}/{sha256}.
    Потоковые upload/download через aioboto3.
    """

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ) -> None:
        self._endpoint_url = endpoint_url
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region

        # Ленивый импорт — aioboto3 в extras [s3]
        try:
            import aioboto3
        except ImportError as exc:
            raise ConfigurationError(
                "S3-хранилище требует aioboto3, который не установлен",
                hint="Установите: pip install orqion[s3]",
            ) from exc

        self._session = aioboto3.Session(  # type: ignore[unused-ignore]
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def _s3_client(self) -> Any:
        """Создаёт S3-клиент с endpoint_url."""
        return self._session.client("s3", endpoint_url=self._endpoint_url)

    def _key(self, sha256_hex: str) -> str:
        """S3-ключ объекта по sha256."""
        return f"{sha256_hex[:2]}/{sha256_hex[2:4]}/{sha256_hex}"

    async def _ensure_bucket(self) -> None:
        """Создаёт bucket при первом обращении, если не существует."""
        async with self._s3_client() as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except Exception:  # noqa: BLE001  bucket does not exist
                await s3.create_bucket(Bucket=self._bucket)

    async def put(self, source: AsyncIterator[bytes]) -> BlobRef:
        """Сохраняет поток байтов в S3, возвращает BlobRef.

        Вычисляет sha256 на лету. Если объект уже существует — не перезаписывает
        (SHA256 гарантирует идемпотентность: тот же контент = тот же ключ).
        """
        await self._ensure_bucket()

        hasher = hashlib.sha256()
        size = 0
        chunks: list[bytes] = []

        async for chunk in source:
            hasher.update(chunk)
            size += len(chunk)
            chunks.append(chunk)

        sha256_hex = hasher.hexdigest()
        key = self._key(sha256_hex)

        async with self._s3_client() as s3:
            # Проверяем существование
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return BlobRef(uri=sha256_hex, sha256=sha256_hex, size=size)
            except Exception:  # noqa: BLE001, S110  head_object 404
                pass

            # Загружаем
            data = b"".join(chunks)
            await s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
            )

        return BlobRef(uri=sha256_hex, sha256=sha256_hex, size=size)

    async def get(self, uri: str) -> AsyncIterator[bytes]:
        """Потоковое чтение blob из S3. Чанками по CHUNK_SIZE.

        Возбуждает KeyError, если blob не найден.
        Вызывающий код обязан ловить KeyError до появления доменного
        исключения в T-204.
        """
        key = self._key(uri)

        async with self._s3_client() as s3:
            try:
                response = await s3.get_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                raise KeyError(f"Blob not found: {uri}") from exc

            body = response["Body"]
            try:
                while True:
                    chunk = await body.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk
            finally:
                body.close()

    async def delete(self, uri: str) -> None:
        """Удаляет blob из S3. Идемпотентна.

        Не проверяет, ссылается ли на blob какой-либо документ —
        это ответственность вызывающего кода (T-204).
        """
        key = self._key(uri)
        async with self._s3_client() as s3:
            try:
                await s3.delete_object(Bucket=self._bucket, Key=key)
            except Exception:  # noqa: BLE001, S110  already deleted
                pass

    async def exists(self, uri: str) -> bool:
        """Проверяет наличие blob в S3."""
        key = self._key(uri)
        async with self._s3_client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:  # noqa: BLE001
                return False
