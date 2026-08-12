"""Тесты таблиц оценки (T-223).

Проверки:
- test_eval_set_create: создание EvalSet с workspace_id и corpus_id
- test_eval_item_create: создание EvalItem с expected_doc_ids
- test_eval_run_create: создание EvalRun с pipeline/metrics
- test_eval_run_index_version_set_null_on_delete: удаление IndexVersion
  обнуляет eval_run.index_version_id (ON DELETE SET NULL), запись сохраняется
- test_eval_set_cascade_delete: удаление EvalSet каскадно удаляет EvalItem и EvalRun
- test_eval_set_unique_name: UniqueConstraint workspace_id + name
- test_eval_schema_roundtrip: Pydantic-схемы корректно сериализуют модели
"""

from __future__ import annotations

import pytest
from app.api.schemas.eval import (
    EvalItemRead,
    EvalRunRead,
    EvalSetCreate,
    EvalSetRead,
)
from app.db.base import _utcnow
from app.db.models import (
    Corpus,
    EvalItem,
    EvalRun,
    EvalSet,
    IndexVersion,
)
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


async def _make_workspace(session: AsyncSession) -> str:
    from app.db.models import Workspace

    ws = Workspace(name="test-ws")
    session.add(ws)
    await session.flush()
    return ws.id


async def _make_corpus(session: AsyncSession, workspace_id: str) -> str:
    corpus = Corpus(workspace_id=workspace_id, name="test-corpus")
    session.add(corpus)
    await session.flush()
    return corpus.id


async def _make_index_version(
    session: AsyncSession,
    workspace_id: str,
    corpus_id: str,
    status: str = "active",
) -> str:
    iv = IndexVersion(
        workspace_id=workspace_id,
        corpus_id=corpus_id,
        embedding_model="BAAI/bge-m3",
        chunker="header",
        chunker_version="1",
        status=status,
    )
    session.add(iv)
    await session.flush()
    return iv.id


# ---------------------------------------------------------------------------
# Тесты моделей
# ---------------------------------------------------------------------------


async def test_eval_set_create(db_session: AsyncSession) -> None:
    """EvalSet создаётся с workspace_id, corpus_id, name."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)

    eval_set = EvalSet(workspace_id=ws_id, corpus_id=corpus_id, name="dataset-1")
    db_session.add(eval_set)
    await db_session.flush()

    assert eval_set.id is not None
    assert eval_set.name == "dataset-1"
    assert eval_set.corpus_id == corpus_id
    assert eval_set.workspace_id == ws_id
    assert eval_set.created_at is not None


async def test_eval_item_create(db_session: AsyncSession) -> None:
    """EvalItem создаётся с question, expected_doc_ids, expected_answer."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)

    eval_set = EvalSet(workspace_id=ws_id, corpus_id=corpus_id, name="dataset-1")
    db_session.add(eval_set)
    await db_session.flush()

    item = EvalItem(
        workspace_id=ws_id,
        eval_set_id=eval_set.id,
        question="What is RAG?",
        expected_doc_ids=["doc-1", "doc-2"],
        expected_answer="Retrieval-Augmented Generation",
    )
    db_session.add(item)
    await db_session.flush()

    assert item.id is not None
    assert item.question == "What is RAG?"
    assert item.expected_doc_ids == ["doc-1", "doc-2"]
    assert item.expected_answer == "Retrieval-Augmented Generation"


async def test_eval_run_create(db_session: AsyncSession) -> None:
    """EvalRun создаётся с pipeline, metrics, ts."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    iv_id = await _make_index_version(db_session, ws_id, corpus_id)

    eval_set = EvalSet(workspace_id=ws_id, corpus_id=corpus_id, name="dataset-1")
    db_session.add(eval_set)
    await db_session.flush()

    run = EvalRun(
        workspace_id=ws_id,
        eval_set_id=eval_set.id,
        index_version_id=iv_id,
        pipeline={"steps": ["rewrite", "search", "rerank", "build_context", "generate"]},
        metrics={"recall@5": 0.8, "ndcg@10": 0.72},
    )
    db_session.add(run)
    await db_session.flush()

    assert run.id is not None
    assert run.index_version_id == iv_id
    assert run.pipeline["steps"] == ["rewrite", "search", "rerank", "build_context", "generate"]
    assert run.metrics is not None
    assert run.metrics["recall@5"] == 0.8
    assert run.ts is not None


async def test_eval_run_index_version_set_null_on_delete(
    db_session: AsyncSession,
) -> None:
    """Удаление IndexVersion (cleanup_retired_versions) обнуляет
    eval_run.index_version_id (ON DELETE SET NULL), запись сохраняется."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    iv_id = await _make_index_version(db_session, ws_id, corpus_id, status="retired")

    eval_set = EvalSet(workspace_id=ws_id, corpus_id=corpus_id, name="dataset-1")
    db_session.add(eval_set)
    await db_session.flush()

    run = EvalRun(
        workspace_id=ws_id,
        eval_set_id=eval_set.id,
        index_version_id=iv_id,
        pipeline={"steps": ["search"]},
        metrics={"recall@5": 0.9},
    )
    db_session.add(run)
    await db_session.flush()
    run_id = run.id

    # Удаление IndexVersion (как cleanup_retired_versions делает session.delete)
    iv = await db_session.get(IndexVersion, iv_id)
    assert iv is not None
    await db_session.delete(iv)
    await db_session.flush()
    # ORM session хранит кэш — сбрасываем, чтобы перечитать из БД
    db_session.expire_all()

    # eval_run сохранён, index_version_id = NULL
    run_after = await db_session.get(EvalRun, run_id)
    assert run_after is not None
    assert run_after.index_version_id is None
    assert run_after.metrics is not None
    assert run_after.metrics["recall@5"] == 0.9


async def test_eval_set_cascade_delete(db_session: AsyncSession) -> None:
    """Удаление EvalSet каскадно удаляет EvalItem и EvalRun."""
    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)
    iv_id = await _make_index_version(db_session, ws_id, corpus_id)

    eval_set = EvalSet(workspace_id=ws_id, corpus_id=corpus_id, name="dataset-1")
    db_session.add(eval_set)
    await db_session.flush()

    item = EvalItem(
        workspace_id=ws_id,
        eval_set_id=eval_set.id,
        question="Q1",
        expected_doc_ids=[],
    )
    run = EvalRun(
        workspace_id=ws_id,
        eval_set_id=eval_set.id,
        index_version_id=iv_id,
        pipeline={},
        metrics=None,
    )
    db_session.add_all([item, run])
    await db_session.flush()
    item_id = item.id
    run_id = run.id

    await db_session.delete(eval_set)
    await db_session.flush()

    assert await db_session.get(EvalItem, item_id) is None
    assert await db_session.get(EvalRun, run_id) is None


async def test_eval_set_unique_name(db_session: AsyncSession) -> None:
    """UniqueConstraint workspace_id + name — нельзя создать два набора с одним именем."""
    from sqlalchemy.exc import IntegrityError

    ws_id = await _make_workspace(db_session)
    corpus_id = await _make_corpus(db_session, ws_id)

    eval_set_1 = EvalSet(workspace_id=ws_id, corpus_id=corpus_id, name="unique-name")
    db_session.add(eval_set_1)
    await db_session.flush()

    eval_set_2 = EvalSet(workspace_id=ws_id, corpus_id=corpus_id, name="unique-name")
    db_session.add(eval_set_2)
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ---------------------------------------------------------------------------
# Тесты схем
# ---------------------------------------------------------------------------


def test_eval_set_create_schema() -> None:
    """EvalSetCreate валидирует corpus_id и name."""
    schema = EvalSetCreate(corpus_id="corp-1", name="test-set")
    assert schema.corpus_id == "corp-1"
    assert schema.name == "test-set"


def test_eval_set_read_schema() -> None:
    """EvalSetRead сериализует модель."""
    now = _utcnow()
    schema = EvalSetRead(
        id="es-1",
        workspace_id="ws-1",
        corpus_id="corp-1",
        name="test-set",
        created_at=now,
    )
    assert schema.id == "es-1"
    assert schema.name == "test-set"


def test_eval_item_read_schema() -> None:
    """EvalItemRead сериализует модель."""
    schema = EvalItemRead(
        id="ei-1",
        workspace_id="ws-1",
        eval_set_id="es-1",
        question="What is RAG?",
        expected_doc_ids=["doc-1"],
        expected_answer="RAG is...",
    )
    assert schema.question == "What is RAG?"
    assert schema.expected_doc_ids == ["doc-1"]


def test_eval_run_read_schema() -> None:
    """EvalRunRead сериализует модель с nullable index_version_id."""
    now = _utcnow()
    schema = EvalRunRead(
        id="er-1",
        workspace_id="ws-1",
        eval_set_id="es-1",
        index_version_id=None,
        pipeline={"steps": ["search"]},
        metrics={"recall@5": 0.8},
        ts=now,
    )
    assert schema.index_version_id is None
    assert schema.metrics is not None
    assert schema.metrics["recall@5"] == 0.8
