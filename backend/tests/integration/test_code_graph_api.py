"""Т-504: граф связей кода — API.

GET /api/corpora/{corpus_id}/code-graph:
- доступ по способности ``view_code_graph`` (без права 404; ``*`` даёт);
- корпус без активной версии → пустой граф;
- несуществующий корпус → 404;
- узлы и рёбра из метаданных активной версии;
- усечение по узлам только явное: ``truncated``, полное число узлов.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Chunk, Corpus, Document, IndexVersion, Role, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI


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
            name=f"graph-{role_name}",
            is_builtin=True,
            policy=policy,
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email=f"graph-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)


async def _seed_corpus_with_code_chunks(
    app_fixture: FastAPI,
    *,
    chunk_count: int = 3,
    with_parent_and_imports: bool = True,
) -> str:
    """Корпус с активной версией индекса и кодовыми чанками."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(name="graph-corpus", workspace_id=workspace_id, data_class="К0")
        session.add(corpus)
        await session.flush()

        doc = Document(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            filename="app.py",
            mime="text/x-python",
            blob_uri="0" * 64,
            sha256="0" * 64,
            size_bytes=10,
            status="ready",
        )
        session.add(doc)
        await session.flush()

        version = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="test-embed",
            chunker="code",
            chunker_version="v1",
            status="active",
            stats={"status": "completed"},
        )
        session.add(version)
        await session.flush()

        corpus.active_index_version_id = version.id

        for i in range(chunk_count):
            meta: dict[str, object] = {
                "file": "app.py",
                "language": "python",
                "symbol": f"symbol_{i}",
            }
            if with_parent_and_imports and i == 0:
                meta["parent"] = f"symbol_{chunk_count}"  # родитель без чанка
                meta["imports"] = ["os", "json"]
            session.add(
                Chunk(
                    workspace_id=workspace_id,
                    index_version_id=version.id,
                    document_id=doc.id,
                    ordinal=i,
                    text=f"chunk {i}",
                    meta=meta,
                )
            )
        await session.commit()
    return corpus.id


@pytest.mark.asyncio
async def test_graph_requires_capability(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Без способности — 404 (существование раздела не раскрывается)."""
    await _login_with_policy(
        api_client, app_fixture, "developer", BUILTIN_ROLES["developer"].model_dump()
    )
    corpus_id = await _seed_corpus_with_code_chunks(app_fixture)

    resp = await api_client.get(f"/api/corpora/{corpus_id}/code-graph")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_graph_allowed_with_explicit_capability(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Способность можно выдать любой роли правкой политики."""
    policy = BUILTIN_ROLES["developer"].model_dump()
    policy["capabilities"] = list(policy["capabilities"]) + ["view_code_graph"]
    await _login_with_policy(api_client, app_fixture, "viewer", policy)
    corpus_id = await _seed_corpus_with_code_chunks(app_fixture)

    resp = await api_client.get(f"/api/corpora/{corpus_id}/code-graph")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_graph_for_admin_wildcard(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_with_policy(api_client, app_fixture, "admin", BUILTIN_ROLES["admin"].model_dump())
    corpus_id = await _seed_corpus_with_code_chunks(app_fixture)

    resp = await api_client.get(f"/api/corpora/{corpus_id}/code-graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus_id"] == corpus_id
    assert body["index_version_id"] is not None
    assert body["truncated"] is False
    assert body["total_nodes"] == body["shown_nodes"]

    node_ids = {n["id"] for n in body["nodes"]}
    assert "chunk:missing" not in node_ids
    labels = {n["label"] for n in body["nodes"]}
    assert "symbol_0" in labels

    kinds = {e["kind"] for e in body["edges"]}
    assert "parent" in kinds
    assert "import" in kinds
    module_targets = {e["target"] for e in body["edges"] if e["kind"] == "import"}
    assert module_targets == {"module:os", "module:json"}


@pytest.mark.asyncio
async def test_graph_empty_without_active_version(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_with_policy(api_client, app_fixture, "admin", BUILTIN_ROLES["admin"].model_dump())
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(name="empty-corpus", workspace_id=workspace_id, data_class="К0")
        session.add(corpus)
        await session.commit()
        corpus_id = corpus.id

    resp = await api_client.get(f"/api/corpora/{corpus_id}/code-graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["index_version_id"] is None
    assert body["nodes"] == []
    assert body["total_nodes"] == 0


@pytest.mark.asyncio
async def test_graph_unknown_corpus_404(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login_with_policy(api_client, app_fixture, "admin", BUILTIN_ROLES["admin"].model_dump())
    resp = await api_client.get("/api/corpora/no-such-id/code-graph")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_graph_truncation_is_explicit(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Больше лимита узлов — усечение с полными числами в ответе."""
    await _login_with_policy(api_client, app_fixture, "admin", BUILTIN_ROLES["admin"].model_dump())
    corpus_id = await _seed_corpus_with_code_chunks(
        app_fixture, chunk_count=305, with_parent_and_imports=False
    )

    resp = await api_client.get(f"/api/corpora/{corpus_id}/code-graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is True
    assert body["total_nodes"] == 305
    chunk_nodes = [n for n in body["nodes"] if n["kind"] == "symbol"]
    assert len(chunk_nodes) == 300
    assert body["shown_nodes"] == len(body["nodes"])
