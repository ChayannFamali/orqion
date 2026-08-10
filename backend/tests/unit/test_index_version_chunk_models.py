"""Тесты моделей IndexVersion и Chunk (T-205).

Проверки:
- IndexVersion создаётся с workspace_id
- status по умолчанию "building"
- stats nullable
- Chunk создаётся с workspace_id
- ordinal обязательный
- text обязательный
- meta nullable
- corpus.active_index_version_id FK → index_version.id (реальная строка)
- CASCADE: удаление index_version удаляет chunks
- CASCADE: удаление corpus удаляет index_version
"""

from __future__ import annotations

import pytest
from app.db.models import Chunk, Corpus, Document, IndexVersion, Workspace
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def _make_workspace(session: AsyncSession, name: str = "test") -> Workspace:
    ws = Workspace(name=name)
    session.add(ws)
    await session.flush()
    return ws


async def _make_corpus(session: AsyncSession, name: str = "Test corpus") -> Corpus:
    ws = await _make_workspace(session)
    corpus = Corpus(name=name)
    corpus.workspace_id = ws.id
    session.add(corpus)
    await session.flush()
    return corpus


async def _make_document(session: AsyncSession, corpus: Corpus) -> Document:
    doc = Document(
        corpus_id=corpus.id,
        blob_uri="abc123",
        filename="test.py",
        mime="text/x-python",
        sha256="abc123def456",
    )
    doc.workspace_id = corpus.workspace_id
    session.add(doc)
    await session.flush()
    return doc


@pytest.mark.asyncio
async def test_index_version_created(db_session: AsyncSession) -> None:
    """IndexVersion создаётся с workspace_id."""
    corpus = await _make_corpus(db_session)
    iv = IndexVersion(
        corpus_id=corpus.id,
        embedding_model="bge-m3",
        chunker="tree_sitter",
        chunker_version="1.0",
    )
    iv.workspace_id = corpus.workspace_id
    db_session.add(iv)
    await db_session.flush()
    assert iv.id is not None
    assert iv.workspace_id is not None
    assert iv.corpus_id == corpus.id


@pytest.mark.asyncio
async def test_index_version_status_default_building(db_session: AsyncSession) -> None:
    """status по умолчанию 'building'."""
    corpus = await _make_corpus(db_session)
    iv = IndexVersion(
        corpus_id=corpus.id,
        embedding_model="bge-m3",
        chunker="tree_sitter",
        chunker_version="1.0",
    )
    iv.workspace_id = corpus.workspace_id
    db_session.add(iv)
    await db_session.flush()
    assert iv.status == "building"


@pytest.mark.asyncio
async def test_index_version_stats_nullable(db_session: AsyncSession) -> None:
    """stats nullable — версия без статистики."""
    corpus = await _make_corpus(db_session)
    iv = IndexVersion(
        corpus_id=corpus.id,
        embedding_model="bge-m3",
        chunker="docling",
        chunker_version="2.0",
    )
    iv.workspace_id = corpus.workspace_id
    db_session.add(iv)
    await db_session.flush()
    assert iv.stats is None


@pytest.mark.asyncio
async def test_index_version_stats_stored(db_session: AsyncSession) -> None:
    """stats сохраняется как JSON."""
    corpus = await _make_corpus(db_session)
    iv = IndexVersion(
        corpus_id=corpus.id,
        embedding_model="bge-m3",
        chunker="docling",
        chunker_version="2.0",
        stats={"chunk_count": 42, "total_tokens": 12000},
    )
    iv.workspace_id = corpus.workspace_id
    db_session.add(iv)
    await db_session.flush()
    assert iv.stats is not None
    assert iv.stats["chunk_count"] == 42


@pytest.mark.asyncio
async def test_chunk_created(db_session: AsyncSession) -> None:
    """Chunk создаётся с workspace_id."""
    corpus = await _make_corpus(db_session)
    doc = await _make_document(db_session, corpus)
    iv = IndexVersion(
        corpus_id=corpus.id,
        embedding_model="bge-m3",
        chunker="tree_sitter",
        chunker_version="1.0",
    )
    iv.workspace_id = corpus.workspace_id
    db_session.add(iv)
    await db_session.flush()

    chunk = Chunk(
        index_version_id=iv.id,
        document_id=doc.id,
        ordinal=0,
        text="def hello(): pass",
        meta={"language": "python", "symbol": "hello"},
    )
    chunk.workspace_id = corpus.workspace_id
    db_session.add(chunk)
    await db_session.flush()
    assert chunk.id is not None
    assert chunk.workspace_id is not None
    assert chunk.ordinal == 0
    assert chunk.meta is not None
    assert chunk.meta["language"] == "python"


@pytest.mark.asyncio
async def test_chunk_meta_nullable(db_session: AsyncSession) -> None:
    """meta nullable — чанк без метаданных."""
    corpus = await _make_corpus(db_session)
    doc = await _make_document(db_session, corpus)
    iv = IndexVersion(
        corpus_id=corpus.id,
        embedding_model="bge-m3",
        chunker="docling",
        chunker_version="1.0",
    )
    iv.workspace_id = corpus.workspace_id
    db_session.add(iv)
    await db_session.flush()

    chunk = Chunk(
        index_version_id=iv.id,
        document_id=doc.id,
        ordinal=1,
        text="Plain text chunk",
    )
    chunk.workspace_id = corpus.workspace_id
    db_session.add(chunk)
    await db_session.flush()
    assert chunk.meta is None


@pytest.mark.asyncio
async def test_corpus_active_index_version_fk(
    db_session: AsyncSession,
) -> None:
    """corpus.active_index_version_id ссылается на реальную строку index_version."""
    corpus = await _make_corpus(db_session)
    iv = IndexVersion(
        corpus_id=corpus.id,
        embedding_model="bge-m3",
        chunker="docling",
        chunker_version="1.0",
    )
    iv.workspace_id = corpus.workspace_id
    db_session.add(iv)
    await db_session.flush()

    corpus.active_index_version_id = iv.id
    await db_session.flush()

    # Перезагружаем corpus из БД
    await db_session.refresh(corpus)
    assert corpus.active_index_version_id == iv.id


@pytest.mark.asyncio
async def test_corpus_active_index_version_invalid_fk(
    db_session: AsyncSession,
) -> None:
    """active_index_version_id с несуществующим ID → IntegrityError."""
    corpus = await _make_corpus(db_session)
    corpus.active_index_version_id = "nonexistent-iv-id"
    db_session.add(corpus)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_delete_index_version_cascades_chunks(
    db_session: AsyncSession,
) -> None:
    """Удаление index_version каскадно удаляет chunks."""
    corpus = await _make_corpus(db_session)
    doc = await _make_document(db_session, corpus)
    iv = IndexVersion(
        corpus_id=corpus.id,
        embedding_model="bge-m3",
        chunker="tree_sitter",
        chunker_version="1.0",
    )
    iv.workspace_id = corpus.workspace_id
    db_session.add(iv)
    await db_session.flush()

    chunk = Chunk(
        index_version_id=iv.id,
        document_id=doc.id,
        ordinal=0,
        text="chunk text",
    )
    chunk.workspace_id = corpus.workspace_id
    db_session.add(chunk)
    await db_session.flush()
    chunk_id = chunk.id

    await db_session.delete(iv)
    await db_session.flush()

    result = await db_session.get(Chunk, chunk_id)
    assert result is None


@pytest.mark.asyncio
async def test_delete_corpus_cascades_index_version(
    db_session: AsyncSession,
) -> None:
    """Удаление корпуса каскадно удаляет index_version."""
    corpus = await _make_corpus(db_session)
    iv = IndexVersion(
        corpus_id=corpus.id,
        embedding_model="bge-m3",
        chunker="docling",
        chunker_version="1.0",
    )
    iv.workspace_id = corpus.workspace_id
    db_session.add(iv)
    await db_session.flush()
    iv_id = iv.id

    await db_session.delete(corpus)
    await db_session.flush()

    result = await db_session.get(IndexVersion, iv_id)
    assert result is None
