"""Тесты переключения версии и отката (T-215, ADR-8).

Проверки:
- activate: активная сменилась, прежняя → retired, audit_log
- rollback: откат возвращает прежнюю версию, search работает
- rollback читает последнюю запись action="index_version.activate"
- rollback после cleanup → IndexVersionGone (явный отказ)
- cleanup удаляет retired-версии: chunks + vectors
- cleanup — отдельная операция от activate
- rollback без предыдущей версии → None
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from app.db.models import AuditLog, Chunk, Corpus, Document, IndexVersion, Role, User, Workspace
from app.errors import IndexVersionGone
from app.rag.embeddings import EmbeddedChunk
from app.rag.service import (
    activate_index_version,
    cleanup_retired_versions,
    rollback_index_version,
)
from app.rag.vector_store import EMBEDDING_DIM, SQLiteVectorStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def vector_store(tmp_path: Path) -> SQLiteVectorStore:
    return SQLiteVectorStore(str(tmp_path / "test_vec.db"))


@pytest.fixture(autouse=True)
async def _close_vector_store(vector_store: SQLiteVectorStore) -> AsyncIterator[None]:
    """Закрывает соединение vector_store после теста.

    Без close() aiosqlite daemon thread не даёт процессу завершиться на Linux CI.
    """
    yield
    await vector_store.close()


def _make_unit_vec(idx: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[idx] = 1.0
    return vec


async def _setup_corpus(
    session: AsyncSession,
    workspace: Workspace,
) -> tuple[Corpus, IndexVersion, IndexVersion, str]:
    """Создаёт корпус с двумя версиями: v1 (active), v2 (building).

    Возвращает (corpus, v1, v2, user_id) — user_id для audit_log FK.
    """
    # Роль + пользователь для audit_log FK (actor_user_id → user.id → role.id)
    role = Role(
        workspace_id=workspace.id,
        name="test-role",
        is_builtin=False,
        policy={},
    )
    session.add(role)
    await session.flush()

    user = User(
        workspace_id=workspace.id,
        email="test@test.com",
        password_hash="hash",
        role_id=role.id,
        is_active=True,
    )
    session.add(user)
    await session.flush()

    corpus = Corpus(workspace_id=workspace.id, name="test-corpus")
    session.add(corpus)
    await session.flush()

    v1 = IndexVersion(
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        embedding_model="test-model",
        chunker="mixed-v1",
        chunker_version="1.0",
        status="active",
        stats={"status": "completed"},
    )
    session.add(v1)
    await session.flush()

    v2 = IndexVersion(
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        embedding_model="test-model",
        chunker="mixed-v1",
        chunker_version="1.0",
        status="building",
        stats={"status": "completed"},
    )
    session.add(v2)
    await session.flush()

    corpus.active_index_version_id = v1.id
    await session.flush()

    return corpus, v1, v2, user.id


async def _add_chunks(
    session: AsyncSession,
    workspace: Workspace,
    version: IndexVersion,
    vector_store: SQLiteVectorStore,
    text: str = "active content",
    sha256: str = "abc123",
) -> Chunk:
    """Добавляет чанк в версию (БД + vector store)."""
    doc = Document(
        workspace_id=workspace.id,
        corpus_id=version.corpus_id,
        blob_uri=f"sha256:{sha256}",
        filename=f"test-{sha256}.md",
        mime="text/markdown",
        sha256=sha256,
        source_type="upload",
        status="pending",
    )
    session.add(doc)
    await session.flush()

    chunk = Chunk(
        workspace_id=workspace.id,
        index_version_id=version.id,
        document_id=doc.id,
        ordinal=0,
        text=text,
        meta={},
    )
    session.add(chunk)
    await session.flush()

    embedded = EmbeddedChunk(
        text=text,
        vector=_make_unit_vec(0),
        ordinal=0,
        model="test",
        chunk_id=chunk.id,
    )
    await vector_store.upsert(version.id, [embedded])
    return chunk


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activate_switches_active_version(
    db_session: AsyncSession,
    vector_store: SQLiteVectorStore,
) -> None:
    """activate: активная сменилась, прежняя → retired."""
    workspace = Workspace(name="test")
    db_session.add(workspace)
    await db_session.flush()

    corpus, v1, v2, user_id = await _setup_corpus(db_session, workspace)

    prev_id = await activate_index_version(
        db_session,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        new_version_id=v2.id,
        actor_user_id=user_id,
    )
    await db_session.commit()

    # previous_version_id = v1
    assert prev_id == v1.id

    # corpus указывает на v2
    fresh_corpus = await db_session.get(Corpus, corpus.id)
    assert fresh_corpus is not None
    assert fresh_corpus.active_index_version_id == v2.id

    # v1 → retired, v2 → active
    fresh_v1 = await db_session.get(IndexVersion, v1.id)
    fresh_v2 = await db_session.get(IndexVersion, v2.id)
    assert fresh_v1 is not None and fresh_v1.status == "retired"
    assert fresh_v2 is not None and fresh_v2.status == "active"


@pytest.mark.asyncio
async def test_activate_writes_audit_log(
    db_session: AsyncSession,
    vector_store: SQLiteVectorStore,
) -> None:
    """activate: запись в audit_log."""
    workspace = Workspace(name="test")
    db_session.add(workspace)
    await db_session.flush()

    corpus, v1, v2, user_id = await _setup_corpus(db_session, workspace)

    await activate_index_version(
        db_session,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        new_version_id=v2.id,
        actor_user_id=user_id,
    )
    await db_session.commit()

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "index_version.activate")
    )
    audits = list(result.scalars().all())
    assert len(audits) == 1
    audit = audits[0]
    assert audit.actor_user_id == user_id
    assert audit.object_type == "corpus"
    assert audit.object_id == corpus.id
    assert audit.meta["old_version_id"] == v1.id
    assert audit.meta["new_version_id"] == v2.id


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_restores_previous(
    db_session: AsyncSession,
    vector_store: SQLiteVectorStore,
) -> None:
    """rollback: откат возвращает прежнюю версию, search работает."""
    workspace = Workspace(name="test")
    db_session.add(workspace)
    await db_session.flush()

    corpus, v1, v2, user_id = await _setup_corpus(db_session, workspace)
    await _add_chunks(db_session, workspace, v1, vector_store, "v1 content", sha256="v1hash")
    await _add_chunks(db_session, workspace, v2, vector_store, "v2 content", sha256="v2hash")
    await db_session.commit()

    # activate: v1 → retired, v2 → active
    await activate_index_version(
        db_session,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        new_version_id=v2.id,
        actor_user_id=user_id,
    )
    await db_session.commit()

    # search по v2
    hits = await vector_store.search_dense(v2.id, _make_unit_vec(0), k=10)
    assert any(h.text == "v2 content" for h in hits)

    # rollback: v2 → retired, v1 → active
    restored_id = await rollback_index_version(
        db_session,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        actor_user_id=user_id,
    )
    await db_session.commit()

    assert restored_id == v1.id

    # corpus указывает на v1
    fresh_corpus = await db_session.get(Corpus, corpus.id)
    assert fresh_corpus is not None
    assert fresh_corpus.active_index_version_id == v1.id

    # v1 → active, v2 → retired
    fresh_v1 = await db_session.get(IndexVersion, v1.id)
    fresh_v2 = await db_session.get(IndexVersion, v2.id)
    assert fresh_v1 is not None and fresh_v1.status == "active"
    assert fresh_v2 is not None and fresh_v2.status == "retired"

    # search по v1 — работает
    hits_v1 = await vector_store.search_dense(v1.id, _make_unit_vec(0), k=10)
    assert any(h.text == "v1 content" for h in hits_v1)


@pytest.mark.asyncio
async def test_rollback_no_previous_returns_none(
    db_session: AsyncSession,
    vector_store: SQLiteVectorStore,
) -> None:
    """rollback без предыдущей activate → None."""
    workspace = Workspace(name="test")
    db_session.add(workspace)
    await db_session.flush()

    corpus, _v1, _, user_id = await _setup_corpus(db_session, workspace)
    await db_session.commit()

    result = await rollback_index_version(
        db_session,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        actor_user_id=user_id,
    )
    assert result is None


@pytest.mark.asyncio
async def test_rollback_after_cleanup_fails_explicitly(
    db_session: AsyncSession,
    vector_store: SQLiteVectorStore,
) -> None:
    """activate → cleanup → rollback → IndexVersionGone, active не изменился."""
    workspace = Workspace(name="test")
    db_session.add(workspace)
    await db_session.flush()

    corpus, v1, v2, user_id = await _setup_corpus(db_session, workspace)
    await _add_chunks(db_session, workspace, v1, vector_store, "v1 content", sha256="v1hash")
    await _add_chunks(db_session, workspace, v2, vector_store, "v2 content", sha256="v2hash")
    await db_session.commit()

    # activate: v1 → retired, v2 → active
    await activate_index_version(
        db_session,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        new_version_id=v2.id,
        actor_user_id=user_id,
    )
    await db_session.commit()

    # cleanup: удаляет v1 (retired) — chunks, vectors, index_version
    deleted = await cleanup_retired_versions(
        db_session,
        vector_store,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        actor_user_id=user_id,
    )
    await db_session.commit()
    assert deleted == 1

    # rollback: v1 удалена → IndexVersionGone
    with pytest.raises(IndexVersionGone) as exc_info:
        await rollback_index_version(
            db_session,
            workspace_id=workspace.id,
            corpus_id=corpus.id,
            actor_user_id=user_id,
        )

    # active_index_version_id не изменился
    fresh_corpus = await db_session.get(Corpus, corpus.id)
    assert fresh_corpus is not None
    assert fresh_corpus.active_index_version_id == v2.id

    # Сообщение содержит информацию о версии
    assert str(v1.id) in str(exc_info.value)


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_removes_retired_versions(
    db_session: AsyncSession,
    vector_store: SQLiteVectorStore,
) -> None:
    """cleanup удаляет retired-версии: chunks + vectors + index_version."""
    workspace = Workspace(name="test")
    db_session.add(workspace)
    await db_session.flush()

    corpus, v1, v2, user_id = await _setup_corpus(db_session, workspace)
    await _add_chunks(db_session, workspace, v1, vector_store, "v1 content", sha256="v1hash")
    await _add_chunks(db_session, workspace, v2, vector_store, "v2 content", sha256="v2hash")
    await db_session.commit()

    # activate: v1 → retired
    await activate_index_version(
        db_session,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        new_version_id=v2.id,
        actor_user_id=user_id,
    )
    await db_session.commit()

    # cleanup: удаляет v1
    deleted = await cleanup_retired_versions(
        db_session,
        vector_store,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        actor_user_id=user_id,
    )
    await db_session.commit()

    assert deleted == 1

    # v1 удалена
    fresh_v1 = await db_session.get(IndexVersion, v1.id)
    assert fresh_v1 is None

    # chunks v1 удалены
    chunks_v1 = await db_session.execute(select(Chunk).where(Chunk.index_version_id == v1.id))
    assert list(chunks_v1.scalars().all()) == []

    # vectors v1 удалены (search возвращает пусто)
    hits = await vector_store.search_dense(v1.id, _make_unit_vec(0), k=10)
    assert hits == []

    # v2 — не затронута
    fresh_v2 = await db_session.get(IndexVersion, v2.id)
    assert fresh_v2 is not None
    assert fresh_v2.status == "active"

    # audit_log cleanup
    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "index_version.cleanup")
    )
    audits = list(result.scalars().all())
    assert len(audits) == 1
    assert audits[0].meta["count"] == 1


@pytest.mark.asyncio
async def test_cleanup_separate_from_activate(
    db_session: AsyncSession,
    vector_store: SQLiteVectorStore,
) -> None:
    """cleanup — отдельная операция от activate, не в одной транзакции."""
    workspace = Workspace(name="test")
    db_session.add(workspace)
    await db_session.flush()

    corpus, _v1, v2, user_id = await _setup_corpus(db_session, workspace)
    await db_session.commit()

    # activate — коммит
    await activate_index_version(
        db_session,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        new_version_id=v2.id,
        actor_user_id=user_id,
    )
    await db_session.commit()

    # cleanup — отдельный коммит
    deleted = await cleanup_retired_versions(
        db_session,
        vector_store,
        workspace_id=workspace.id,
        corpus_id=corpus.id,
        actor_user_id=user_id,
    )
    await db_session.commit()

    # Две отдельные записи в audit_log
    result = await db_session.execute(select(AuditLog).where(AuditLog.object_id == corpus.id))
    audits = list(result.scalars().all())
    actions = [a.action for a in audits]
    assert "index_version.activate" in actions
    assert "index_version.cleanup" in actions
    assert deleted == 1
