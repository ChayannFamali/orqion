"""Тесты моделей Corpus и Document (T-203).

Проверки:
- Corpus создаётся с workspace_id
- data_class сохраняется
- pinned_model_id nullable, FK
- active_index_version_id nullable (без FK до T-205)
- Document создаётся с workspace_id и corpus_id
- status по умолчанию "pending"
- UniqueConstraint (workspace_id, sha256) — дубликат → IntegrityError
- corpus_id ON DELETE CASCADE — удаление корпуса удаляет документы
- одинаковый sha256 в разных workspace — не дубликат
"""

from __future__ import annotations

import pytest
from app.db.models import Corpus, Document, Workspace
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def _make_workspace(session: AsyncSession, name: str = "test") -> Workspace:
    ws = Workspace(name=name)
    session.add(ws)
    await session.flush()
    return ws


async def _make_corpus(
    session: AsyncSession,
    name: str = "Test corpus",
    data_class: str | None = None,
) -> Corpus:
    ws = await _make_workspace(session)
    corpus = Corpus(name=name, data_class=data_class)
    corpus.workspace_id = ws.id
    session.add(corpus)
    await session.flush()
    return corpus


@pytest.mark.asyncio
async def test_corpus_created(db_session: AsyncSession) -> None:
    """Corpus создаётся с workspace_id."""
    corpus = await _make_corpus(db_session)
    assert corpus.id is not None
    assert corpus.name == "Test corpus"
    assert corpus.workspace_id is not None


@pytest.mark.asyncio
async def test_corpus_data_class(db_session: AsyncSession) -> None:
    """data_class сохраняется и читается."""
    corpus = await _make_corpus(db_session, "Secret", "К3")
    assert corpus.data_class == "К3"


@pytest.mark.asyncio
async def test_corpus_data_class_nullable(db_session: AsyncSession) -> None:
    """data_class nullable — корпус без класса данных."""
    corpus = await _make_corpus(db_session, "No class")
    assert corpus.data_class is None


@pytest.mark.asyncio
async def test_corpus_pinned_model_nullable(db_session: AsyncSession) -> None:
    """pinned_model_id nullable по умолчанию."""
    corpus = await _make_corpus(db_session, "No pin")
    assert corpus.pinned_model_id is None


@pytest.mark.asyncio
async def test_corpus_active_index_version_nullable(
    db_session: AsyncSession,
) -> None:
    """active_index_version_id nullable — корпус без индекса."""
    corpus = await _make_corpus(db_session, "Empty")
    assert corpus.active_index_version_id is None


@pytest.mark.asyncio
async def test_document_created(db_session: AsyncSession) -> None:
    """Document создаётся с workspace_id и corpus_id."""
    corpus = await _make_corpus(db_session, "Docs")
    doc = Document(
        corpus_id=corpus.id,
        blob_uri="abc123",
        filename="test.pdf",
        mime="application/pdf",
        sha256="abc123def456",
        source_type="upload",
    )
    doc.workspace_id = corpus.workspace_id
    db_session.add(doc)
    await db_session.flush()
    assert doc.id is not None
    assert doc.workspace_id == corpus.workspace_id
    assert doc.status == "pending"
    assert doc.corpus_id == corpus.id


@pytest.mark.asyncio
async def test_document_status_default_pending(db_session: AsyncSession) -> None:
    """status по умолчанию 'pending'."""
    corpus = await _make_corpus(db_session, "Status test")
    doc = Document(
        corpus_id=corpus.id,
        blob_uri="sha",
        filename="f.txt",
        mime="text/plain",
        sha256="unique_sha_1",
    )
    doc.workspace_id = corpus.workspace_id
    db_session.add(doc)
    await db_session.flush()
    assert doc.status == "pending"


@pytest.mark.asyncio
async def test_document_deduplication(db_session: AsyncSession) -> None:
    """UniqueConstraint (corpus_id, sha256) — дубликат в тот же корпус → IntegrityError."""
    corpus = await _make_corpus(db_session, "Dedup")
    doc1 = Document(
        corpus_id=corpus.id,
        blob_uri="sha1",
        filename="a.txt",
        mime="text/plain",
        sha256="same_sha",
    )
    doc1.workspace_id = corpus.workspace_id
    db_session.add(doc1)
    await db_session.flush()

    doc2 = Document(
        corpus_id=corpus.id,
        blob_uri="sha1",
        filename="b.txt",
        mime="text/plain",
        sha256="same_sha",
    )
    doc2.workspace_id = corpus.workspace_id
    db_session.add(doc2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_corpus_delete_cascades_documents(
    db_session: AsyncSession,
) -> None:
    """Удаление корпуса каскадно удаляет документы."""
    corpus = await _make_corpus(db_session, "To delete")
    doc = Document(
        corpus_id=corpus.id,
        blob_uri="sha",
        filename="f.txt",
        mime="text/plain",
        sha256="cascade_sha",
    )
    doc.workspace_id = corpus.workspace_id
    db_session.add(doc)
    await db_session.flush()
    doc_id = doc.id

    await db_session.delete(corpus)
    await db_session.flush()

    result = await db_session.get(Document, doc_id)
    assert result is None


@pytest.mark.asyncio
async def test_document_different_workspace_same_sha(
    db_session: AsyncSession,
) -> None:
    """Одинаковый sha256 в разных workspace — не дубликат."""
    ws1 = await _make_workspace(db_session, "ws1")
    ws2 = await _make_workspace(db_session, "ws2")

    corpus1 = Corpus(name="C1")
    corpus1.workspace_id = ws1.id
    corpus2 = Corpus(name="C2")
    corpus2.workspace_id = ws2.id
    db_session.add_all([corpus1, corpus2])
    await db_session.flush()

    doc1 = Document(
        corpus_id=corpus1.id,
        blob_uri="shared",
        filename="a.txt",
        mime="text/plain",
        sha256="shared_sha",
    )
    doc1.workspace_id = ws1.id
    doc2 = Document(
        corpus_id=corpus2.id,
        blob_uri="shared",
        filename="b.txt",
        mime="text/plain",
        sha256="shared_sha",
    )
    doc2.workspace_id = ws2.id
    db_session.add_all([doc1, doc2])
    await db_session.flush()

    assert doc1.id != doc2.id


@pytest.mark.asyncio
async def test_same_content_different_corpus_allowed(
    db_session: AsyncSession,
) -> None:
    """Одинаковый sha256 в разных корпусах одного workspace — не дубликат."""
    ws = await _make_workspace(db_session, "shared")
    corpus1 = Corpus(name="C1")
    corpus1.workspace_id = ws.id
    corpus2 = Corpus(name="C2")
    corpus2.workspace_id = ws.id
    db_session.add_all([corpus1, corpus2])
    await db_session.flush()

    doc1 = Document(
        corpus_id=corpus1.id,
        blob_uri="shared_blob",
        filename="readme.md",
        mime="text/markdown",
        sha256="same_content_sha",
    )
    doc1.workspace_id = ws.id
    doc2 = Document(
        corpus_id=corpus2.id,
        blob_uri="shared_blob",
        filename="readme.md",
        mime="text/markdown",
        sha256="same_content_sha",
    )
    doc2.workspace_id = ws.id
    db_session.add_all([doc1, doc2])
    await db_session.flush()

    assert doc1.id != doc2.id
