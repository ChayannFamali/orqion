"""Хранилище оригиналов документов (ADR-7).

Интерфейс BlobStore: put, get, delete, exists.
Ключ — sha256 содержимого (голый hex, без схемы URI).
Оригинал сохраняется до начала разбора.
Повторная загрузка не дублируется.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

CHUNK_SIZE = 64 * 1024  # 64 KB


@dataclass(frozen=True)
class BlobRef:
    """Ссылка на сохранённый blob.

    uri — голый sha256 hex, без схемы. Формат определяется реализацией,
    но не используется для диспетчеризации (выбора бэкенда).
    """

    uri: str
    sha256: str
    size: int


@runtime_checkable
class BlobStore(Protocol):
    """Хранилище оригиналов документов.

    ADR-7: оригиналы — источник правды, индекс — производное.
    Blob store хранит байты и ничего не знает о документах.
    Метаданные (filename, mime) — в таблице document, не здесь.
    """

    async def put(self, source: AsyncIterator[bytes]) -> BlobRef:
        """Сохраняет поток байтов, возвращает BlobRef.

        Вычисляет sha256 на лету. Если blob с таким sha256 уже существует —
        не дублирует, возвращает существующий.
        """
        ...

    async def get(self, uri: str) -> AsyncIterator[bytes]:
        """Потоковое чтение blob по uri. Возбуждает KeyError, если не найден."""
        ...

    async def delete(self, uri: str) -> None:
        """Удаляет blob. Идемпотентна.

        Не проверяет, ссылается ли на blob какой-либо документ —
        это ответственность вызывающего кода (T-204).
        """
        ...

    async def exists(self, uri: str) -> bool:
        """Проверяет наличие blob по uri."""
        ...


class LocalBlobStore:
    """Локальная файловая реализация BlobStore.

    Шардирование: ab/cd/{sha256} (первые 2+2 символа hex).
    Запись во временный файл, затем атомарное переименование.
    Прерванный put не оставляет валидного blob (exists → False).
    """

    def __init__(self, root: str) -> None:
        self._root = root

    def _path(self, sha256_hex: str) -> str:
        """Внутренний путь к blob по sha256."""
        return f"{self._root}/{sha256_hex[:2]}/{sha256_hex[2:4]}/{sha256_hex}"

    async def put(self, source: AsyncIterator[bytes]) -> BlobRef:
        """Сохраняет поток байтов, возвращает BlobRef."""
        import asyncio
        import os
        import tempfile

        from anyio import Path

        hasher = hashlib.sha256()
        size = 0

        # Каталог для временных файлов
        temp_dir = f"{self._root}/.tmp"
        await Path(temp_dir).mkdir(parents=True, exist_ok=True)

        # Временный файл — имя неизвестно до завершения потока
        temp_path = tempfile.mktemp(dir=temp_dir)
        loop = asyncio.get_event_loop()

        try:
            def _open() -> Any:
                return open(temp_path, "wb")

            f = await loop.run_in_executor(None, _open)
            try:
                async for chunk in source:
                    hasher.update(chunk)
                    size += len(chunk)

                    def _write(c: bytes = chunk) -> None:
                        f.write(c)

                    await loop.run_in_executor(None, _write)

                def _flush_fsync() -> None:
                    f.flush()
                    os.fsync(f.fileno())

                await loop.run_in_executor(None, _flush_fsync)
            finally:
                await loop.run_in_executor(None, f.close)

            sha256_hex = hasher.hexdigest()
            final_path = self._path(sha256_hex)
            final_anyio_path = Path(final_path)

            # Если blob уже существует — удаляем temp, возвращаем существующий
            if await final_anyio_path.exists():
                os.unlink(temp_path)
                return BlobRef(uri=sha256_hex, sha256=sha256_hex, size=size)

            # Создаём каталог и атомарно переименовываем
            await Path(os.path.dirname(final_path)).mkdir(parents=True, exist_ok=True)
            os.rename(temp_path, final_path)

            return BlobRef(uri=sha256_hex, sha256=sha256_hex, size=size)
        except Exception:
            # Очистка temp при ошибке
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    async def get(self, uri: str) -> AsyncIterator[bytes]:
        """Потоковое чтение blob по uri (sha256 hex). Чанками по CHUNK_SIZE."""
        import asyncio

        from anyio import Path

        path = self._path(uri)
        if not await Path(path).exists():
            raise KeyError(f"Blob not found: {uri}")

        loop = asyncio.get_event_loop()

        def _open_file() -> Any:
            return open(path, "rb")

        f = await loop.run_in_executor(None, _open_file)
        try:
            while True:
                chunk = await loop.run_in_executor(None, lambda: f.read(CHUNK_SIZE))
                if not chunk:
                    break
                yield chunk
        finally:
            await loop.run_in_executor(None, f.close)

    async def delete(self, uri: str) -> None:
        """Удаляет blob. Идемпотентна.

        Не проверяет, ссылается ли на blob какой-либо документ —
        это ответственность вызывающего кода (T-204).
        """
        from anyio import Path

        path = self._path(uri)
        try:
            await Path(path).unlink()
        except FileNotFoundError:
            pass

    async def exists(self, uri: str) -> bool:
        """Проверяет наличие blob по uri."""
        from anyio import Path

        return await Path(self._path(uri)).exists()
