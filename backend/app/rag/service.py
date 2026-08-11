"""Сервисный слой RAG: загрузка документов (T-204).

Слои: api → service → repository (SQLAlchemy).
Service orchestrates BlobStore + DB, без обращений к БД из роутера.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import _utcnow
from app.db.models import AuditLog, Chunk, Corpus, Document, IndexVersion
from app.errors import (
    CorpusNotReady,
    DuplicateDocument,
    FileTooLarge,
    FileTypeNotAllowed,
    IndexVersionGone,
    NotFound,
)
from app.rag.blob import BlobRef, BlobStore
from app.rag.vector_store import VectorStore


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


# ---------------------------------------------------------------------------
# Переключение версии индекса и откат (T-215, ADR-8)
# ---------------------------------------------------------------------------


async def activate_index_version(
    session: AsyncSession,
    *,
    workspace_id: str,
    corpus_id: str,
    new_version_id: str,
    actor_user_id: str,
) -> str | None:
    """Атомарное переключение corpus.active_index_version_id (ADR-8).

    1. new_version (building) → active
    2. прежняя active → retired
    3. corpus.active_index_version_id = new_version_id
    4. audit_log: index_version.activate
    Одна транзакция — переключение + audit, без удаления.

    Returns:
        previous_version_id — для отката, или None если активной версии не было.
    """
    corpus = await session.get(Corpus, corpus_id)
    if corpus is None or corpus.workspace_id != workspace_id:
        raise NotFound(
            constraint={"object": "corpus", "id": corpus_id},
            hint="Корпус не найден",
        )

    new_version = await session.get(IndexVersion, new_version_id)
    if new_version is None or new_version.workspace_id != workspace_id:
        raise NotFound(
            constraint={"object": "index_version", "id": new_version_id},
            hint="Версия индекса не найдена",
        )

    previous_version_id = corpus.active_index_version_id

    # Прежняя active → retired
    if previous_version_id is not None:
        old_version = await session.get(IndexVersion, previous_version_id)
        if old_version is not None and old_version.status == "active":
            old_version.status = "retired"

    # Новая → active
    new_version.status = "active"

    # Переключение указателя
    corpus.active_index_version_id = new_version_id

    # Audit log
    audit = AuditLog(
        workspace_id=workspace_id,
        ts=_utcnow(),
        actor_user_id=actor_user_id,
        action="index_version.activate",
        object_type="corpus",
        object_id=corpus_id,
        meta={
            "old_version_id": previous_version_id,
            "new_version_id": new_version_id,
        },
    )
    session.add(audit)
    await session.flush()

    return previous_version_id


async def rollback_index_version(
    session: AsyncSession,
    *,
    workspace_id: str,
    corpus_id: str,
    actor_user_id: str,
) -> str | None:
    """Откат к предыдущей версии индекса (ADR-8).

    Читает последнюю запись audit_log с action="index_version.activate"
    (игнорируя cleanup и другие действия между).
    Проверяет, что целевая версия физически существует и status=retired.
    Если версия удалена через cleanup → IndexVersionGone (явный отказ).

    Returns:
        Восстановленный version_id, или None если откатывать некуда.
    """
    corpus = await session.get(Corpus, corpus_id)
    if corpus is None or corpus.workspace_id != workspace_id:
        raise NotFound(
            constraint={"object": "corpus", "id": corpus_id},
            hint="Корпус не найден",
        )

    # Последняя запись activate
    result = await session.execute(
        select(AuditLog)
        .where(
            AuditLog.workspace_id == workspace_id,
            AuditLog.object_id == corpus_id,
            AuditLog.action == "index_version.activate",
        )
        .order_by(AuditLog.ts.desc())
        .limit(1)
    )
    audit_entry = result.scalar_one_or_none()
    if audit_entry is None:
        return None  # откатывать некуда — не было activate

    meta = audit_entry.meta
    previous_version_id = meta.get("old_version_id")
    current_version_id = meta.get("new_version_id")
    if previous_version_id is None:
        return None  # предыдущей версии не было

    # Проверка: целевая версия физически существует
    target_version = await session.get(IndexVersion, str(previous_version_id))
    if target_version is None or target_version.status != "retired":
        raise IndexVersionGone(
            f"Версия индекса {previous_version_id} удалена и не может быть восстановлена",
            constraint={
                "version_id": previous_version_id,
                "current_status": target_version.status if target_version else "deleted",
            },
            hint="Версия была удалена через cleanup_retired_versions. "
            "Откат невозможен — постройте новую версию индекса.",
        )

    # Текущая active → retired
    if current_version_id is not None:
        current_version = await session.get(IndexVersion, str(current_version_id))
        if current_version is not None and current_version.status == "active":
            current_version.status = "retired"

    # Восстанавливаемая → active
    target_version.status = "active"

    # Переключение указателя
    corpus.active_index_version_id = str(previous_version_id)

    # Audit log
    new_audit = AuditLog(
        workspace_id=workspace_id,
        ts=_utcnow(),
        actor_user_id=actor_user_id,
        action="index_version.rollback",
        object_type="corpus",
        object_id=corpus_id,
        meta={
            "restored_version_id": previous_version_id,
            "retired_version_id": current_version_id,
        },
    )
    session.add(new_audit)
    await session.flush()

    return str(previous_version_id)


async def cleanup_retired_versions(
    session: AsyncSession,
    vector_store: VectorStore,
    *,
    workspace_id: str,
    corpus_id: str,
    actor_user_id: str,
) -> int:
    """Удаление retired-версий: chunks + vectors + index_version.

    Отдельная транзакция от activate (требование приёмки).
    Не автоматический шаг после activate — вызывается явно администратором.
    cleanup делает откат удалённой версии невозможным (IndexVersionGone).

    Returns:
        Количество удалённых версий.
    """
    result = await session.execute(
        select(IndexVersion).where(
            IndexVersion.workspace_id == workspace_id,
            IndexVersion.corpus_id == corpus_id,
            IndexVersion.status == "retired",
        )
    )
    retired_versions = list(result.scalars().all())

    for version in retired_versions:
        # Удаление chunks
        await session.execute(select(Chunk).where(Chunk.index_version_id == version.id))
        # Удаление векторов
        await vector_store.drop_version(version.id)
        # Удаление index_version
        await session.delete(version)

    # Audit log
    audit = AuditLog(
        workspace_id=workspace_id,
        ts=_utcnow(),
        actor_user_id=actor_user_id,
        action="index_version.cleanup",
        object_type="corpus",
        object_id=corpus_id,
        meta={
            "deleted_version_ids": [v.id for v in retired_versions],
            "count": len(retired_versions),
        },
    )
    session.add(audit)
    await session.flush()

    return len(retired_versions)


async def resolve_corpus(
    session: AsyncSession,
    workspace_id: str,
    corpus_name: str,
) -> Corpus:
    """Загружает корпус по имени в рамках workspace.

    Возбуждает NotFound — корпус не существует.
    Возбуждает CorpusNotReady — нет активной версии индекса.
    """
    result = await session.execute(
        select(Corpus).where(
            Corpus.workspace_id == workspace_id,
            Corpus.name == corpus_name,
        )
    )
    corpus = result.scalar_one_or_none()
    if corpus is None:
        raise NotFound(
            constraint={"object": "corpus", "name": corpus_name},
            hint="Корпус не найден",
        )
    if corpus.active_index_version_id is None:
        raise CorpusNotReady(
            constraint={"corpus": corpus_name},
            hint="Корпус не имеет активной версии индекса",
        )
    return corpus
