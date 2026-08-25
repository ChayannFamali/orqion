"""Удаление корпуса со всем содержимым.

Проверки:
- удаление корпуса с документами, версией индекса (чанки + векторы),
  наборами оценки → 200; все строки удалены, файлы и векторы очищены,
  запись аудита создана;
- общий файл двух корпусов: при удалении одного корпусов файл сохраняется,
  при удалении второго — удаляется;
- без права manage_corpora → 404;
- несуществующий корпус → 404.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import (
    AuditLog,
    Chunk,
    Corpus,
    Document,
    EvalItem,
    EvalRun,
    EvalSet,
    IndexVersion,
    Role,
    User,
)
from app.policy.presets import BUILTIN_ROLES
from app.rag.embeddings import EmbeddedChunk
from fastapi import FastAPI
from sqlalchemy import func, select


async def _login_with_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str = "admin",
    policy: dict[str, Any] | None = None,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role_policy = policy or BUILTIN_ROLES[role_name].model_dump()
        role = Role(
            workspace_id=workspace_id,
            name=f"corpus-del-{role_name}",
            is_builtin=True,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email=f"corpus-del-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _create_corpus(app_fixture: FastAPI, name: str) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(name=name, workspace_id=workspace_id, data_class=None)
        session.add(corpus)
        await session.flush()
        corpus_id = corpus.id
        await session.commit()
    return corpus_id


async def _upload_file(
    api_client: httpx.AsyncClient,
    corpus_id: str,
    *,
    filename: str = "test.txt",
    content: bytes = b"Hello orqion",
) -> httpx.Response:
    files = {"file": (filename, content, "text/plain")}
    return await api_client.post(
        f"/api/corpora/{corpus_id}/documents",
        files=files,
    )


async def _seed_index_and_eval(app_fixture: FastAPI, corpus_id: str) -> tuple[str, str]:
    """Версия индекса с чанком + набор оценки. Возвращает (version_id, eval_set_id)."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        doc = (
            await session.execute(select(Document).where(Document.corpus_id == corpus_id).limit(1))
        ).scalar_one()

        version = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            embedding_model="test-embed",
            chunker="doc",
            chunker_version="v1",
            status="completed",
            stats={"status": "completed"},
        )
        session.add(version)
        await session.flush()

        chunk = Chunk(
            workspace_id=workspace_id,
            index_version_id=version.id,
            document_id=doc.id,
            ordinal=0,
            text="hello world",
            meta={},
        )
        session.add(chunk)

        eval_set = EvalSet(workspace_id=workspace_id, corpus_id=corpus_id, name="qs")
        session.add(eval_set)
        await session.flush()

        item = EvalItem(
            workspace_id=workspace_id,
            eval_set_id=eval_set.id,
            question="Вопрос?",
            expected_doc_ids=[doc.id],
        )
        session.add(item)

        run = EvalRun(
            workspace_id=workspace_id,
            eval_set_id=eval_set.id,
            index_version_id=version.id,
            pipeline={},
        )
        session.add(run)
        await session.commit()
        return version.id, eval_set.id


@pytest.mark.asyncio
async def test_delete_corpus_full_cleanup(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Корпус удаляется вместе с документами, индексами, оценками, файлами."""
    await _login_with_role(api_client, app_fixture, role_name="architect")
    corpus_id = await _create_corpus(app_fixture, "public")

    upload = await _upload_file(api_client, corpus_id)
    assert upload.status_code == 201, upload.text
    blob_uri = upload.json()["blob_uri"]
    blob_store = app_fixture.state.blob_store
    vector_store = app_fixture.state.vector_store
    assert await blob_store.exists(blob_uri)

    version_id, eval_set_id = await _seed_index_and_eval(app_fixture, corpus_id)
    # Вектор в хранилище версии (размерность хранилища фиксирована)
    await vector_store.upsert(
        version_id,
        [
            EmbeddedChunk(
                text="hello world",
                vector=[0.1] * 1024,
                ordinal=0,
                model="test-embed",
                chunk_id="00000000-0000-0000-0000-000000000001",
            )
        ],
    )
    hits_before = await vector_store.search_sparse(version_id, "hello")
    assert len(hits_before) == 1

    resp = await api_client.delete(f"/api/corpora/{corpus_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": True}

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        assert await session.get(Corpus, corpus_id) is None
        docs = (
            await session.scalar(
                select(func.count(Document.id)).where(Document.corpus_id == corpus_id)
            )
        ) or 0
        assert docs == 0
        versions = (
            await session.scalar(
                select(func.count(IndexVersion.id)).where(IndexVersion.corpus_id == corpus_id)
            )
        ) or 0
        assert versions == 0
        chunks = (
            await session.scalar(
                select(func.count(Chunk.id)).where(Chunk.index_version_id == version_id)
            )
        ) or 0
        assert chunks == 0
        eval_sets = (
            await session.scalar(
                select(func.count(EvalSet.id)).where(EvalSet.corpus_id == corpus_id)
            )
        ) or 0
        assert eval_sets == 0
        eval_items = (
            await session.scalar(
                select(func.count(EvalItem.id)).where(EvalItem.eval_set_id == eval_set_id)
            )
        ) or 0
        assert eval_items == 0
        eval_runs = (
            await session.scalar(
                select(func.count(EvalRun.id)).where(EvalRun.eval_set_id == eval_set_id)
            )
        ) or 0
        assert eval_runs == 0
        audit = await session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.object_id == corpus_id,
                AuditLog.action == "corpus.delete",
            )
        )
        assert audit == 1

    # Файл и векторы удалены
    assert not await blob_store.exists(blob_uri)
    hits_after = await vector_store.search_sparse(version_id, "hello")
    assert hits_after == []


@pytest.mark.asyncio
async def test_delete_corpus_keeps_shared_blob(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Общий файл двух корпусов живёт, пока на него ссылается другой корпус."""
    await _login_with_role(api_client, app_fixture, role_name="architect")
    corpus_a = await _create_corpus(app_fixture, "public")
    corpus_b = await _create_corpus(app_fixture, "team")

    content = b"shared content"
    upload_a = await _upload_file(api_client, corpus_a, content=content)
    assert upload_a.status_code == 201, upload_a.text
    upload_b = await _upload_file(api_client, corpus_b, content=content)
    assert upload_b.status_code == 201, upload_b.text

    blob_uri = upload_a.json()["blob_uri"]
    assert upload_b.json()["blob_uri"] == blob_uri
    blob_store = app_fixture.state.blob_store
    assert await blob_store.exists(blob_uri)

    resp_a = await api_client.delete(f"/api/corpora/{corpus_a}")
    assert resp_a.status_code == 200
    # Второй корпус ещё ссылается на файл — файл сохранён
    assert await blob_store.exists(blob_uri)

    resp_b = await api_client.delete(f"/api/corpora/{corpus_b}")
    assert resp_b.status_code == 200
    assert not await blob_store.exists(blob_uri)


@pytest.mark.asyncio
async def test_delete_corpus_denied_without_capability(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_with_role(api_client, app_fixture, role_name="support")
    corpus_id = await _create_corpus(app_fixture, "protected")

    resp = await api_client.delete(f"/api/corpora/{corpus_id}")
    assert resp.status_code == 404

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        assert await session.get(Corpus, corpus_id) is not None


@pytest.mark.asyncio
async def test_delete_corpus_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_with_role(api_client, app_fixture, role_name="architect")

    resp = await api_client.delete("/api/corpora/nonexistent-corpus-id")
    assert resp.status_code == 404
