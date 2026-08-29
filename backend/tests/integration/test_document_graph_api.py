"""Т-505: граф связей документов (семантические кластеры) — API.

GET /api/corpora/{corpus_id}/document-graph:
- доступ по способности ``view_document_graph`` (без права 404; ``*`` даёт);
- корпус без активной версии → доступный пустой ответ;
- несуществующий корпус → 404;
- узлы-группы и узлы-документы из кластеризации активной версии;
- повторный вызов читает кэш (``from_cache=true``) без пересчёта;
- число групп берётся из настроек рабочей области (``cluster_count``);
- деградация: без установленной экстры ``orqion[graph]`` эндпоинт
  отвечает ``available=false`` с явной причиной, а не падает.

Тесты кластеризации пропускаются без установленного numpy; гейт,
пустые состояния и деградация проверяются в обоих профилях — ровно
поведение дефолтного профиля CI (без экстр).
"""

from __future__ import annotations

import sys
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import (
    Chunk,
    Corpus,
    Document,
    IndexVersion,
    RagSettings,
    Role,
    User,
)
from app.rag.embeddings import EmbeddedChunk
from app.rag.vector_store import EMBEDDING_DIM
from fastapi import FastAPI


def _unit_vec(index: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[index % EMBEDDING_DIM] = 1.0
    return vec


async def _login_with_policy(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
    policy: dict[str, Any],
) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name=f"docgraph-{role_name}",
            is_builtin=True,
            policy=policy,
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email=f"docgraph-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)


async def _seed_corpus_with_documents(
    app_fixture: FastAPI,
    *,
    doc_specs: list[tuple[str, int, int]],
    cluster_count: int | None = None,
) -> str:
    """Корпус с активной версией, документами, чанками и векторами.

    ``doc_specs`` — список (имя файла, число чанков, индекс направления
    вектора). Векторы с одинаковым направлением кластеризуются вместе.
    ``cluster_count`` — если задан, записывается в настройки области.
    """
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    vector_store = app_fixture.state.vector_store
    async with factory() as session:
        corpus = Corpus(name="docgraph-corpus", workspace_id=workspace_id, data_class="К0")
        session.add(corpus)
        await session.flush()

        version = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="test-embed",
            chunker="header",
            chunker_version="1.2",
            status="active",
            stats={"status": "completed"},
        )
        session.add(version)
        await session.flush()

        corpus.active_index_version_id = version.id

        if cluster_count is not None:
            session.add(RagSettings(workspace_id=workspace_id, cluster_count=cluster_count))

        embedded: list[EmbeddedChunk] = []
        ordinal_global = 0
        for doc_index, (filename, chunk_count, direction) in enumerate(doc_specs):
            doc = Document(
                workspace_id=workspace_id,
                corpus_id=corpus.id,
                filename=filename,
                mime="text/markdown",
                blob_uri="0" * 64,
                # Уникальность документа — по (corpus_id, sha256); хеш свой у каждого.
                sha256=f"{doc_index:064x}",
                size_bytes=10,
                status="ready",
            )
            session.add(doc)
            await session.flush()
            for i in range(chunk_count):
                chunk = Chunk(
                    workspace_id=workspace_id,
                    index_version_id=version.id,
                    document_id=doc.id,
                    ordinal=i,
                    text=f"{filename} chunk {i}",
                    meta={"heading_path": [], "chunker": "header"},
                )
                session.add(chunk)
                await session.flush()
                embedded.append(
                    EmbeddedChunk(
                        text=chunk.text,
                        vector=_unit_vec(direction),
                        ordinal=ordinal_global,
                        model="test-embed",
                        chunk_id=chunk.id,
                    )
                )
                ordinal_global += 1
        await session.commit()

    await vector_store.upsert(version.id, embedded)
    return corpus.id


@pytest.mark.asyncio
async def test_graph_requires_capability(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    corpus_id = await _seed_corpus_with_documents(app_fixture, doc_specs=[("a.md", 1, 0)])
    await _login_with_policy(api_client, app_fixture, "denied", {"models": ["*"]})
    resp = await api_client.get(f"/api/corpora/{corpus_id}/document-graph")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_graph_allows_wildcard(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    pytest.importorskip("numpy")
    corpus_id = await _seed_corpus_with_documents(app_fixture, doc_specs=[("a.md", 1, 0)])
    await _login_with_policy(
        api_client, app_fixture, "wild", {"models": ["*"], "capabilities": ["*"]}
    )
    resp = await api_client.get(f"/api/corpora/{corpus_id}/document-graph")
    assert resp.status_code == 200
    assert resp.json()["available"] is True


@pytest.mark.asyncio
async def test_graph_allows_explicit_capability(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    corpus_id = await _seed_corpus_with_documents(app_fixture, doc_specs=[("a.md", 1, 0)])
    await _login_with_policy(
        api_client, app_fixture, "cap", {"capabilities": ["view_document_graph"]}
    )
    resp = await api_client.get(f"/api/corpora/{corpus_id}/document-graph")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_graph_missing_corpus_404(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_with_policy(api_client, app_fixture, "wild", {"capabilities": ["*"]})
    resp = await api_client.get("/api/corpora/00000000-0000-0000-0000-000000000000/document-graph")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_graph_without_active_version_empty(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(name="empty-corpus", workspace_id=workspace_id, data_class="К0")
        session.add(corpus)
        await session.commit()
        corpus_id = corpus.id
    await _login_with_policy(api_client, app_fixture, "wild", {"capabilities": ["*"]})
    resp = await api_client.get(f"/api/corpora/{corpus_id}/document-graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["index_version_id"] is None
    assert body["nodes"] == []
    assert body["edges"] == []


@pytest.mark.asyncio
async def test_graph_clusters_documents(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    # Два документа с одним направлением, один с другим → минимум 2 группы.
    pytest.importorskip("numpy")
    corpus_id = await _seed_corpus_with_documents(
        app_fixture,
        doc_specs=[("a.md", 2, 0), ("b.md", 1, 0), ("c.md", 2, 5)],
        cluster_count=2,
    )
    await _login_with_policy(api_client, app_fixture, "wild", {"capabilities": ["*"]})
    resp = await api_client.get(f"/api/corpora/{corpus_id}/document-graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["cluster_count"] == 2
    assert body["total_documents"] == 3
    assert body["shown_documents"] == 3
    assert body["truncated"] is False

    clusters = [n for n in body["nodes"] if n["kind"] == "cluster"]
    docs = [n for n in body["nodes"] if n["kind"] == "document"]
    assert len(docs) == 3
    assert len(clusters) >= 1
    # Каждое ребро «группа → документ» ссылается на существующий узел.
    node_ids = {n["id"] for n in body["nodes"]}
    assert len(clusters) == len({c["id"] for c in clusters})
    for edge in body["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids
        assert edge["kind"] == "member"


@pytest.mark.asyncio
async def test_graph_second_call_uses_cache(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    pytest.importorskip("numpy")
    corpus_id = await _seed_corpus_with_documents(
        app_fixture,
        doc_specs=[("a.md", 1, 0), ("b.md", 1, 5)],
        cluster_count=2,
    )
    await _login_with_policy(api_client, app_fixture, "wild", {"capabilities": ["*"]})

    first = await api_client.get(f"/api/corpora/{corpus_id}/document-graph")
    assert first.status_code == 200
    assert first.json()["from_cache"] is False

    second = await api_client.get(f"/api/corpora/{corpus_id}/document-graph")
    assert second.status_code == 200
    body = second.json()
    assert body["from_cache"] is True
    assert body["shown_documents"] == 2


@pytest.mark.asyncio
async def test_graph_default_cluster_count_when_no_settings_row(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    pytest.importorskip("numpy")
    corpus_id = await _seed_corpus_with_documents(app_fixture, doc_specs=[("a.md", 1, 0)])
    await _login_with_policy(api_client, app_fixture, "wild", {"capabilities": ["*"]})
    resp = await api_client.get(f"/api/corpora/{corpus_id}/document-graph")
    assert resp.status_code == 200
    # Нет строки настроек → дефолт 8.
    assert resp.json()["cluster_count"] == 8


@pytest.mark.asyncio
async def test_graph_degraded_without_numpy(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_id = await _seed_corpus_with_documents(app_fixture, doc_specs=[("a.md", 1, 0)])
    await _login_with_policy(api_client, app_fixture, "wild", {"capabilities": ["*"]})

    monkeypatch.setitem(sys.modules, "numpy", None)
    resp = await api_client.get(f"/api/corpora/{corpus_id}/document-graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "orqion[graph]" in body["reason"]
