"""Сервисный слой RAG: загрузка документов (T-204).

Слои: api → service → repository (SQLAlchemy).
Service orchestrates BlobStore + DB, без обращений к БД из роутера.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Corpus, Document
from app.errors import DuplicateDocument, FileTooLarge, FileTypeNotAllowed, NotFound
from app.rag.blob import BlobRef, BlobStore


@dataclass(frozen=True)
class UploadResult:
    """Результат загрузки документа."""

    document: Document
    blob_ref: BlobRef


async def upload_document(
    session: AsyncSession,
    blob_store: BlobStore,
    *,
    workspace_id: str,
    corpus_id: str,
    filename: str,
    mime: str,
    content: AsyncIterator[bytes],
    max_size_bytes: int,
    allowed_extensions: list[str],
) -> UploadResult:
    """Загружает документ в корпус.

    Порядок (ADR-7):
    1. Проверка корпуса
    2. Проверка расширения файла
    3. Стриминг в BlobStore через обёртку с лимитом размера (оригинал — первым действием)
    4. Проверка дубликата (corpus_id, sha256)
    5. Создание Document(status=pending)
    """
    # 1. Проверка корпуса
    corpus = await session.get(Corpus, corpus_id)
    if corpus is None or corpus.workspace_id != workspace_id:
        raise NotFound(
            constraint={"object": "corpus", "id": corpus_id},
            hint="Корпус не найден",
        )

    # 2. Проверка расширения
    _check_extension(filename, allowed_extensions)

    # 3. Стриминг в BlobStore с подсчётом размера на лету
    sized_content = _SizedIterator(content, max_size_bytes)
    blob_ref = await blob_store.put(sized_content.iter())

    # 4. Проверка дубликата по (corpus_id, sha256)
    existing = await session.execute(
        select(Document).where(
            Document.corpus_id == corpus_id,
            Document.sha256 == blob_ref.sha256,
        )
    )
    existing_doc = existing.scalar_one_or_none()
    if existing_doc is not None:
        raise DuplicateDocument(
            constraint={"sha256": blob_ref.sha256, "document_id": existing_doc.id},
            hint=f"Документ уже загружен: {existing_doc.filename}",
        )

    # 5. Создание Document
    document = Document(
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        blob_uri=blob_ref.uri,
        filename=filename,
        mime=mime,
        sha256=blob_ref.sha256,
        source_type="upload",
        status="pending",
    )
    session.add(document)
    await session.flush()

    return UploadResult(document=document, blob_ref=blob_ref)


async def list_documents(
    session: AsyncSession,
    *,
    workspace_id: str,
    corpus_id: str,
) -> list[Document]:
    """Возвращает документы корпуса."""
    corpus = await session.get(Corpus, corpus_id)
    if corpus is None or corpus.workspace_id != workspace_id:
        raise NotFound(
            constraint={"object": "corpus", "id": corpus_id},
            hint="Корпус не найден",
        )

    result = await session.execute(
        select(Document)
        .where(
            Document.workspace_id == workspace_id,
            Document.corpus_id == corpus_id,
        )
        .order_by(Document.uploaded_at.desc())
    )
    return list(result.scalars().all())


def _check_extension(filename: str, allowed_extensions: list[str]) -> None:
    """Проверяет расширение файла по списку разрешённых."""
    lower = filename.lower()
    if not any(lower.endswith(ext) for ext in allowed_extensions):
        raise FileTypeNotAllowed(
            constraint={
                "filename": filename,
                "allowed_extensions": allowed_extensions,
            },
            hint=f"Допустимые расширения: {', '.join(allowed_extensions)}",
        )


class _SizedIterator:
    """Обёртка над AsyncIterator[bytes] с подсчётом размера и лимитом.

    Не буферизирует файл целиком в памяти: отдаёт чанки по мере поступления,
    считая накопленный размер. При превышении max_bytes бросает FileTooLarge
    изнутри генератора — BlobStore.put() прерывается, temp-файл очищается.
    """

    def __init__(self, source: AsyncIterator[bytes], max_bytes: int) -> None:
        self._source = source
        self._max_bytes = max_bytes
        self._total = 0

    async def iter(self) -> AsyncIterator[bytes]:
        async for chunk in self._source:
            self._total += len(chunk)
            if self._total > self._max_bytes:
                raise FileTooLarge(
                    constraint={
                        "max_size_bytes": self._max_bytes,
                        "actual_bytes": self._total,
                    },
                    hint=f"Максимальный размер файла: {self._max_bytes // (1024 * 1024)} МБ",
                )
            yield chunk
