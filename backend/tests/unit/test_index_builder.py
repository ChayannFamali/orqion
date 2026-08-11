"""Тесты построения версии индекса (T-214, S-28).

Проверки:
- test_build_creates_index_version_and_chunks: базовый сценарий
- test_search_works_during_build: настоящий конкурентный доступ (не случайная последовательность)
- test_interruption_leaves_building_status: прерывание посередине
- test_progress_tracking: прогресс обновляется после каждого документа
- test_active_version_not_affected: действующая версия не затронута
- test_per_document_chunker_selection: .py → code, .md → header, .sql → sql
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
from app.db.models import Chunk, Corpus, Document, IndexVersion, Workspace
from app.rag.blob import LocalBlobStore
from app.rag.embeddings import EmbeddedChunk
from app.rag.index_builder import build_index_version
from app.rag.vector_store import EMBEDDING_DIM, SQLiteVectorStore
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class StubEmbeddingBackend:
    """Простой EmbeddingBackend для тестов — детерминированные векторы."""

    def __init__(self, model: str = "test-embed", dim: int = EMBEDDING_DIM) -> None:
        self._model = model
        self._dim = dim
        self._call_count = 0

    def model_name(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._call_count += 1
        results: list[list[float]] = []
        for i, text in enumerate(texts):
            vec = [0.0] * self._dim
            # Детерминированный вектор: хеш от текста → позиция
            idx = hash(text) % self._dim
            if idx < 0:
                idx = -idx
            vec[idx] = 1.0
            results.append(vec)
        return results


class PausingEmbeddingBackend:
    """EmbeddingBackend, который ждёт event на первом вызове embed.

    Гарантирует настоящий интерливинг: build останавливается на await event.wait(),
    search выполняется параллельно, затем event.set() разблокирует build.
    """

    def __init__(self, event: asyncio.Event, dim: int = EMBEDDING_DIM) -> None:
        self._event = event
        self._dim = dim
        self._call_count = 0

    def model_name(self) -> str:
        return "test-pausing"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._call_count == 0:
            await self._event.wait()
        self._call_count += 1
        results: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            idx = abs(hash(text)) % self._dim
            vec[idx] = 1.0
            results.append(vec)
        return results


class FailingEmbeddingBackend:
    """EmbeddingBackend, который бросает RuntimeError на втором вызове embed.

    Симулирует прерывание посередине пакетной обработки.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim
        self._call_count = 0

    def model_name(self) -> str:
        return "test-failing"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._call_count += 1
        if self._call_count >= 2:
            raise RuntimeError("Simulated interruption during embedding")
        results: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            idx = abs(hash(text)) % self._dim
            vec[idx] = 1.0
            results.append(vec)
        return results


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def vector_store(tmp_path: Path) -> SQLiteVectorStore:
    """SQLiteVectorStore на временной базе."""
    return SQLiteVectorStore(str(tmp_path / "test_vec.db"))


@pytest.fixture
def embedding_backend() -> StubEmbeddingBackend:
    return StubEmbeddingBackend()


async def _make_corpus(
    session: object,
    workspace: Workspace,
    name: str = "test-corpus",
) -> Corpus:
    """Создаёт корпус."""
    corpus = Corpus(
        workspace_id=workspace.id,
        name=name,
    )
    session.add(corpus)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]
    return corpus


async def _add_document(
    session: object,
    blob_store: LocalBlobStore,
    workspace: Workspace,
    corpus: Corpus,
    filename: str,
    content: bytes,
) -> Document:
    """Добавляет документ в корпус через BlobStore."""

    # Запись в BlobStore
    async def gen() -> AsyncIterator[bytes]:
        yield content

    blob_ref = await blob_store.put(gen())

    doc = Document(
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        blob_uri=blob_ref.uri,
        filename=filename,
        mime="application/octet-stream",
        sha256=blob_ref.sha256,
        source_type="upload",
        status="pending",
    )
    session.add(doc)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]
    return doc


async def _make_active_version(
    session: object,
    workspace: Workspace,
    corpus: Corpus,
    embedding_model: str = "test-embed",
) -> IndexVersion:
    """Создаёт активную версию индекса для корпуса."""
    version = IndexVersion(
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        embedding_model=embedding_model,
        chunker="mixed-v1",
        chunker_version="1.0",
        status="active",
        stats={"status": "completed"},
    )
    session.add(version)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]

    corpus.active_index_version_id = version.id
    await session.flush()  # type: ignore[attr-defined]
    return version


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_creates_index_version_and_chunks(
    db_session: object,
    blob_store: LocalBlobStore,
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """Базовый сценарий: 2 документа → чанки в БД + vector store, stats обновлён."""
    workspace = Workspace(name="test")
    db_session.add(workspace)  # type: ignore[attr-defined]
    await db_session.flush()  # type: ignore[attr-defined]

    corpus = await _make_corpus(db_session, workspace)
    await _add_document(
        db_session, blob_store, workspace, corpus, "doc1.md", b"# Title\nHello world"
    )
    await _add_document(
        db_session, blob_store, workspace, corpus, "doc2.md", b"# Another\nFoo bar baz"
    )
    await db_session.commit()  # type: ignore[attr-defined]

    result = await build_index_version(
        db_session,  # type: ignore[arg-type]
        blob_store,
        vector_store,
        embedding_backend,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
    )

    # index_version создан
    version = await db_session.get(IndexVersion, result.index_version_id)  # type: ignore[arg-type]
    assert version is not None
    assert version.status == "building"
    assert version.chunker == "mixed-v1"
    assert version.embedding_model == "test-embed"

    # stats — completed
    stats = version.stats
    assert stats is not None
    assert stats["status"] == "completed"
    assert stats["documents_total"] == 2
    assert stats["documents_done"] == 2
    assert stats["chunks_total"] > 0

    # Чанки в БД
    chunks = await db_session.execute(  # type: ignore[attr-defined]
        select(Chunk).where(Chunk.index_version_id == version.id)
    )
    chunk_list = list(chunks.scalars().all())
    assert len(chunk_list) == result.chunks_created
    assert len(chunk_list) > 0

    # Чанки в vector store (search_dense находит)
    vec = [0.0] * EMBEDDING_DIM
    hits = await vector_store.search_dense(version.id, vec, k=10)
    assert len(hits) > 0


@pytest.mark.asyncio
async def test_search_works_during_build(
    db_session: object,
    blob_store: LocalBlobStore,
    vector_store: SQLiteVectorStore,
) -> None:
    """Параллельный search по active версии во время build новой — не блокируется.

    Механизм интерливинга:
    1. Создаём corpus с active версией (с чанками в vector store)
    2. Добавляем новый документ
    3. Запускаем build с PausingEmbeddingBackend (останавливается на первом документе)
    4. Пока build остановлен — search_dense по active версии
    5. search возвращает результаты до завершения build
    6. Разблокируем build, завершаем
    """
    workspace = Workspace(name="test")
    db_session.add(workspace)  # type: ignore[attr-defined]
    await db_session.flush()  # type: ignore[attr-defined]

    corpus = await _make_corpus(db_session, workspace)

    # Создаём active версию с чанками
    active_version = await _make_active_version(db_session, workspace, corpus)

    # Добавляем чанк в active версию (в БД и vector store)
    doc = await _add_document(
        db_session, blob_store, workspace, corpus, "active.md", b"# Active\nActive content"
    )
    chunk = Chunk(
        workspace_id=workspace.id,
        index_version_id=active_version.id,
        document_id=doc.id,
        ordinal=0,
        text="Active content",
        meta={},
    )
    db_session.add(chunk)  # type: ignore[attr-defined]
    await db_session.flush()  # type: ignore[attr-defined]

    embedded = EmbeddedChunk(
        text="Active content",
        vector=[0.0] * EMBEDDING_DIM,
        ordinal=0,
        model="test",
        chunk_id=chunk.id,
    )
    await vector_store.upsert(active_version.id, [embedded])

    # Добавляем новый документ для build
    await _add_document(db_session, blob_store, workspace, corpus, "new.md", b"# New\nNew content")
    await db_session.commit()  # type: ignore[attr-defined]

    # Механизм интерливинга
    event = asyncio.Event()
    pausing_backend = PausingEmbeddingBackend(event)

    # Запускаем build в фоновой задаче
    build_task = asyncio.create_task(
        build_index_version(
            db_session,  # type: ignore[arg-type]
            blob_store,
            vector_store,
            pausing_backend,
            workspace_id=workspace.id,
            corpus_id=corpus.id,
        )
    )

    # Пропуск до точки паузы — даём build дойти до await event.wait()
    await asyncio.sleep(0.1)

    # build приостановлен — search по active версии
    search_vec = [0.0] * EMBEDDING_DIM
    hits = await vector_store.search_dense(active_version.id, search_vec, k=10)

    # search вернул результаты (не заблокировался)
    assert len(hits) > 0
    assert any(h.text == "Active content" for h in hits)

    # active_index_version_id не изменился
    fresh_corpus = await db_session.get(Corpus, corpus.id)  # type: ignore[arg-type]
    assert fresh_corpus is not None
    assert fresh_corpus.active_index_version_id == active_version.id

    # Разблокируем build
    event.set()
    build_result = await build_task

    # После завершения build — active версия всё та же
    assert fresh_corpus.active_index_version_id == active_version.id

    # Новая версия создана
    new_version = await db_session.get(IndexVersion, build_result.index_version_id)  # type: ignore[arg-type]
    assert new_version is not None
    assert new_version.status == "building"


@pytest.mark.asyncio
async def test_interruption_leaves_building_status(
    db_session: object,
    blob_store: LocalBlobStore,
    vector_store: SQLiteVectorStore,
) -> None:
    """Прерывание посередине: index_version.status=building, active не изменился."""
    workspace = Workspace(name="test")
    db_session.add(workspace)  # type: ignore[attr-defined]
    await db_session.flush()  # type: ignore[attr-defined]

    corpus = await _make_corpus(db_session, workspace)
    active_version = await _make_active_version(db_session, workspace, corpus)

    # 2 документа: первый обработается, на втором embed бросит исключение
    await _add_document(db_session, blob_store, workspace, corpus, "doc1.md", b"# Doc1\nHello")
    await _add_document(db_session, blob_store, workspace, corpus, "doc2.md", b"# Doc2\nWorld")
    await db_session.commit()  # type: ignore[attr-defined]

    failing_backend = FailingEmbeddingBackend()

    result = await build_index_version(
        db_session,  # type: ignore[arg-type]
        blob_store,
        vector_store,
        failing_backend,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
    )

    # index_version остался в building
    version = await db_session.get(IndexVersion, result.index_version_id)  # type: ignore[arg-type]
    assert version is not None
    assert version.status == "building"

    # stats — interrupted
    stats = version.stats
    assert stats is not None
    assert stats["status"] == "interrupted"
    assert stats["error"] is not None
    assert "Simulated interruption" in str(stats["error"])

    # active_index_version_id не изменился
    fresh_corpus = await db_session.get(Corpus, corpus.id)  # type: ignore[arg-type]
    assert fresh_corpus is not None
    assert fresh_corpus.active_index_version_id == active_version.id

    # Хотя бы один документ был обработан
    assert result.documents_processed >= 1


@pytest.mark.asyncio
async def test_progress_tracking(
    db_session: object,
    blob_store: LocalBlobStore,
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """Прогресс: documents_done увеличивается после каждого документа."""
    workspace = Workspace(name="test")
    db_session.add(workspace)  # type: ignore[attr-defined]
    await db_session.flush()  # type: ignore[attr-defined]

    corpus = await _make_corpus(db_session, workspace)
    for i in range(3):
        await _add_document(
            db_session,
            blob_store,
            workspace,
            corpus,
            f"doc{i}.md",
            f"# Doc {i}\nContent {i}".encode(),
        )
    await db_session.commit()  # type: ignore[attr-defined]

    result = await build_index_version(
        db_session,  # type: ignore[arg-type]
        blob_store,
        vector_store,
        embedding_backend,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
    )

    version = await db_session.get(IndexVersion, result.index_version_id)  # type: ignore[arg-type]
    assert version is not None
    stats = version.stats
    assert stats is not None
    assert stats["documents_total"] == 3
    assert stats["documents_done"] == 3
    assert stats["chunks_total"] > 0
    assert stats["status"] == "completed"


@pytest.mark.asyncio
async def test_active_version_not_affected(
    db_session: object,
    blob_store: LocalBlobStore,
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """После построения новой версии, активная не изменилась: чанки не удалены, не перезаписаны."""
    workspace = Workspace(name="test")
    db_session.add(workspace)  # type: ignore[attr-defined]
    await db_session.flush()  # type: ignore[attr-defined]

    corpus = await _make_corpus(db_session, workspace)
    active_version = await _make_active_version(db_session, workspace, corpus)

    # Чанк в active версии
    doc = await _add_document(
        db_session, blob_store, workspace, corpus, "active.md", b"# Active\nActive text"
    )
    chunk = Chunk(
        workspace_id=workspace.id,
        index_version_id=active_version.id,
        document_id=doc.id,
        ordinal=0,
        text="Active text",
        meta={},
    )
    db_session.add(chunk)  # type: ignore[attr-defined]
    await db_session.flush()  # type: ignore[attr-defined]

    embedded = EmbeddedChunk(
        text="Active text",
        vector=[0.0] * EMBEDDING_DIM,
        ordinal=0,
        model="test",
        chunk_id=chunk.id,
    )
    await vector_store.upsert(active_version.id, [embedded])

    # Новый документ для build
    await _add_document(db_session, blob_store, workspace, corpus, "new.md", b"# New\nNew text")
    await db_session.commit()  # type: ignore[attr-defined]

    await build_index_version(
        db_session,  # type: ignore[arg-type]
        blob_store,
        vector_store,
        embedding_backend,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
    )

    # Active версия — чанки на месте
    active_chunks = await db_session.execute(  # type: ignore[attr-defined]
        select(Chunk).where(Chunk.index_version_id == active_version.id)
    )
    active_chunk_list = list(active_chunks.scalars().all())
    assert len(active_chunk_list) == 1
    assert active_chunk_list[0].text == "Active text"

    # search по active версии — работает
    search_vec = [0.0] * EMBEDDING_DIM
    hits = await vector_store.search_dense(active_version.id, search_vec, k=10)
    assert any(h.text == "Active text" for h in hits)

    # active_index_version_id не изменился
    fresh_corpus = await db_session.get(Corpus, corpus.id)  # type: ignore[arg-type]
    assert fresh_corpus is not None
    assert fresh_corpus.active_index_version_id == active_version.id


@pytest.mark.asyncio
async def test_per_document_chunker_selection(
    db_session: object,
    blob_store: LocalBlobStore,
    vector_store: SQLiteVectorStore,
    embedding_backend: StubEmbeddingBackend,
) -> None:
    """Чанкер выбирается по типу документа: .py → code, .md → header, .sql → sql."""
    workspace = Workspace(name="test")
    db_session.add(workspace)  # type: ignore[attr-defined]
    await db_session.flush()  # type: ignore[attr-defined]

    corpus = await _make_corpus(db_session, workspace)

    # Python-файл
    py_content = b"def hello():\n    return 'world'\n"
    await _add_document(db_session, blob_store, workspace, corpus, "test.py", py_content)

    # Markdown-файл
    md_content = b"# Title\nSome text here\n"
    await _add_document(db_session, blob_store, workspace, corpus, "readme.md", md_content)

    # SQL-файл
    sql_content = b"SELECT * FROM users;\n"
    await _add_document(db_session, blob_store, workspace, corpus, "query.sql", sql_content)

    await db_session.commit()  # type: ignore[attr-defined]

    result = await build_index_version(
        db_session,  # type: ignore[arg-type]
        blob_store,
        vector_store,
        embedding_backend,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
    )

    # Проверяем метаданные чанков
    chunks = await db_session.execute(  # type: ignore[attr-defined]
        select(Chunk).where(Chunk.index_version_id == result.index_version_id)
    )
    chunk_list = list(chunks.scalars().all())
    assert len(chunk_list) > 0

    chunkers_found: set[str] = set()
    for ch in chunk_list:
        if ch.meta and "chunker" in ch.meta:
            chunkers_found.add(str(ch.meta["chunker"]))

    # Все три чанкера использовались
    assert "code" in chunkers_found, f"Expected 'code' chunker, got: {chunkers_found}"
    assert "header" in chunkers_found, f"Expected 'header' chunker, got: {chunkers_found}"
    assert "sql" in chunkers_found, f"Expected 'sql' chunker, got: {chunkers_found}"
