"""Построение версии индекса (T-214, S-28).

Оркестрация: документы корпуса → парсинг → чанкинг → эмбеддинг → БД + vector store.
Прогресс — в index_version.stats. Прерывание оставляет статус building,
действующая версия не затронута (ADR-8).

Чанкер выбирается по типу документа внутри _process_document, не единый на весь
корпус: .py/.cpp/.ts/.go/.java → chunk_code (ADR-9), .sql → chunk_sql,
остальное → chunk_document (заголовки). index_version.chunker — метка конфигурации
("mixed-v1"), не литеральный диспетчер.

arch.md §8.1: файл → blob store → document(status=pending) → парсинг → чанкинг
→ метаданные → эмбеддинг → index_version(status=building) → eval → переключение.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Corpus, Document, IndexVersion
from app.errors import NotFound
from app.rag.blob import BlobStore
from app.rag.chunker import chunk_document
from app.rag.code_chunker import CodeChunk, chunk_code
from app.rag.embeddings import EmbeddedChunk, EmbeddingBackend, embed_batch
from app.rag.parser import parse_document
from app.rag.sql_chunker import SqlChunk, chunk_sql
from app.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

# Метка конфигурации чанкинга — фиксируется в index_version.chunker.
# Авто-выбор по типу документа: code → tree-sitter, sql → tree-sitter,
# документы → заголовки. При изменении алгоритма — новая index_version.
CHUNKER_LABEL = "mixed-v1"

# Расширения для код-чанкера (tree-sitter, ADR-9)
_CODE_EXTENSIONS = {".py", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".ts", ".tsx", ".go", ".java"}

# Расширения для SQL-чанкера
_SQL_EXTENSIONS = {".sql"}


@dataclass
class BuildProgress:
    """Прогресс построения — сериализуется в index_version.stats."""

    documents_total: int = 0
    documents_done: int = 0
    chunks_total: int = 0
    status: str = "building"  # building | completed | interrupted
    error: str | None = None
    current_document: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "documents_total": self.documents_total,
            "documents_done": self.documents_done,
            "chunks_total": self.chunks_total,
            "status": self.status,
            "error": self.error,
            "current_document": self.current_document,
        }


@dataclass
class BuildResult:
    """Результат построения версии индекса."""

    index_version_id: str
    chunks_created: int
    documents_processed: int


async def build_index_version(
    session: AsyncSession,
    blob_store: BlobStore,
    vector_store: VectorStore,
    embedding_backend: EmbeddingBackend,
    *,
    workspace_id: str,
    corpus_id: str,
    embedding_model: str | None = None,
) -> BuildResult:
    """Создаёт и наполняет новую index_version для корпуса.

    1. Создать index_version(status=building)
    2. Для каждого документа: parse → chunk → embed → persist (DB + vector store)
    3. Обновлять stats после каждого документа
    4. При прерывании — stats.status=interrupted, index_version.status=building,
       corpus.active_index_version_id не меняется

    Returns:
        BuildResult с index_version_id и количеством чанков.
    """
    # 1. Проверка корпуса
    corpus = await session.get(Corpus, corpus_id)
    if corpus is None or corpus.workspace_id != workspace_id:
        raise NotFound(
            constraint={"object": "corpus", "id": corpus_id},
            hint="Корпус не найден",
        )

    # 2. Создание index_version
    model_name = embedding_model or embedding_backend.model_name()
    index_version = IndexVersion(
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        embedding_model=model_name,
        chunker=CHUNKER_LABEL,
        chunker_version="1.0",
        status="building",
        stats=BuildProgress(status="building").to_dict(),
    )
    session.add(index_version)
    await session.flush()
    index_version_id = index_version.id

    # 3. Список документов.
    # Документы, помеченные на удаление (статусы, кроме указанных),
    # исключаются из сборки — механизм отложенного удаления.
    result = await session.execute(
        select(Document)
        .where(
            Document.workspace_id == workspace_id,
            Document.corpus_id == corpus_id,
            Document.status.in_(("pending", "indexing", "ready", "failed")),
        )
        .order_by(Document.uploaded_at)
    )
    documents = list(result.scalars().all())

    progress = BuildProgress(
        documents_total=len(documents),
        status="building",
    )

    chunks_total = 0
    docs_done = 0

    for doc in documents:
        progress.current_document = doc.filename
        await _update_stats(session, index_version_id, progress)

        doc.status = "indexing"
        doc.error = None
        await session.flush()

        try:
            chunks_created = await _process_document(
                session,
                blob_store,
                vector_store,
                embedding_backend,
                workspace_id=workspace_id,
                index_version_id=index_version_id,
                document=doc,
            )
            chunks_total += chunks_created
            docs_done += 1
            progress.documents_done = docs_done
            progress.chunks_total = chunks_total
            await _update_stats(session, index_version_id, progress)

            doc.status = "ready"
            await session.flush()
        except Exception as exc:  # noqa: BLE001 — граница системы: парсер/эмбеддер/vector store
            # Прерывание построения: stats фиксирует ошибку, статус остаётся building.
            # Это не suppress — ошибка регистрируется и логируется, построение останавливается.
            progress.status = "interrupted"
            progress.error = str(exc)
            await _update_stats(session, index_version_id, progress)

            index_version.status = "interrupted"
            doc.status = "failed"
            doc.error = str(exc)
            await session.flush()

            logger.warning(
                "Index build interrupted for corpus %s, version %s: %s",
                corpus_id,
                index_version_id,
                exc,
            )
            return BuildResult(
                index_version_id=index_version_id,
                chunks_created=chunks_total,
                documents_processed=docs_done,
            )

    # 4. Завершение
    progress.status = "completed"
    progress.current_document = None
    await _update_stats(session, index_version_id, progress)

    index_version.status = "completed"
    await session.flush()

    return BuildResult(
        index_version_id=index_version_id,
        chunks_created=chunks_total,
        documents_processed=docs_done,
    )


async def _process_document(
    session: AsyncSession,
    blob_store: BlobStore,
    vector_store: VectorStore,
    embedding_backend: EmbeddingBackend,
    *,
    workspace_id: str,
    index_version_id: str,
    document: Document,
) -> int:
    """Обработка одного документа: parse → chunk → embed → persist.

    Возвращает количество созданных чанков.
    """
    # 1. Получение контента: для кода/SQL — raw bytes, для документов — парсинг в markdown
    ext = Path(document.filename).suffix.lower()

    if ext in _CODE_EXTENSIONS or ext in _SQL_EXTENSIONS:
        # Код и SQL — читаем raw bytes напрямую, без Docling
        raw_bytes = bytearray()
        async for part in blob_store.get(document.blob_uri):
            raw_bytes.extend(part)
        source = bytes(raw_bytes)
        if not source.strip():
            document.status = "failed"
            document.error = "Пустой файл"
            await session.flush()
            return 0
    else:
        # Документы — парсинг через Docling/direct
        parse_result = await parse_document(
            blob_store,
            sha256=document.sha256,
            filename=document.filename,
            blob_uri=document.blob_uri,
        )
        if parse_result.error or not parse_result.markdown.strip():
            logger.info(
                "Skipping document %s: parse error or empty content: %s",
                document.filename,
                parse_result.error,
            )
            document.status = "failed"
            document.error = parse_result.error or "Пустой контент после разбора"
            await session.flush()
            return 0
        source = parse_result.markdown.encode("utf-8")

    # 2. Чанкинг — выбор по типу документа
    raw_chunks: Any = None
    chunker_label = "header"
    header_parser = "direct"

    if ext in _CODE_EXTENSIONS:
        raw_chunks = chunk_code(source, document.filename)
        chunker_label = "code"
    elif ext in _SQL_EXTENSIONS:
        raw_chunks = chunk_sql(source, document.filename)
        chunker_label = "sql"
    else:
        header_parser = parse_result.parser
        raw_chunks = chunk_document(source.decode("utf-8"), parser=header_parser)
        chunker_label = "header"

    chunk_texts: list[str] = []
    chunk_metas: list[dict[str, object]] = []
    for c in raw_chunks:
        chunk_texts.append(c.text)
        extra_meta: dict[str, object] = {}
        if isinstance(c, CodeChunk):
            extra_meta = {
                "symbol": c.symbol,
                "parent": c.parent,
                "signature": c.signature,
            }
            # Т-504: импорты чанка — рёбра графа связей кода. Чанкер
            # извлекал их всегда; в метаданные не писались. Старые версии
            # индекса поля не имеют — для них рёбра импортов не
            # показываются (без принудительной пересборки).
            if c.imports:
                extra_meta["imports"] = list(c.imports)
        elif isinstance(c, SqlChunk):
            extra_meta = {
                "operation": c.operation,
                "tables": c.tables,
            }
        chunk_metas.append(
            {
                **c.meta,
                **extra_meta,
                "chunker": chunker_label,
                "document_filename": document.filename,
            }
        )

    if not chunk_texts:
        return 0

    # 3. Эмбеддинг
    embedded = await embed_batch(
        chunk_texts,
        embedding_backend,
        batch_size=32,
    )

    # 4. Запись в БД (Chunk) + vector store
    for i, (emb, text, meta) in enumerate(zip(embedded, chunk_texts, chunk_metas, strict=True)):
        chunk = Chunk(
            workspace_id=workspace_id,
            index_version_id=index_version_id,
            document_id=document.id,
            ordinal=i,
            text=text,
            meta=meta,
        )
        session.add(chunk)
        await session.flush()  # получаем chunk.id

        embedded_chunk = EmbeddedChunk(
            text=text,
            vector=emb.vector,
            ordinal=i,
            model=emb.model,
            chunk_id=chunk.id,
        )
        await vector_store.upsert(index_version_id, [embedded_chunk])

    return len(chunk_texts)


async def _update_stats(
    session: AsyncSession,
    index_version_id: str,
    progress: BuildProgress,
) -> None:
    """Обновляет index_version.stats."""
    await session.execute(
        update(IndexVersion)
        .where(IndexVersion.id == index_version_id)
        .values(stats=progress.to_dict())
    )
    await session.flush()
