"""T-430: ProviderEmbeddingBackend через реальный lifespan-путь.

Integration-тест: embeddings_backend=provider → app_fixture конструирует
ProviderEmbeddingBackend через resolve_embedding_backend (alias → Model →
Provider из БД), как в lifespan. httpx перехватывается MockTransport —
CI не бьёт в реальный внешний провайдер (N-1).

Пункт 4 (обязательное условие приёмки): тест идёт через реально
подключённый app.state.embedding_backend, не напрямую конструируя
ProviderEmbeddingBackend() в обход lifespan-пути.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Corpus, Document, Role, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI


async def _login_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email="t430-admin@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_corpus_with_document(app_fixture: FastAPI) -> str:
    """Создаёт Corpus с одним Document (.txt). Provider+Model уже засеяны в _build_app.

    Возвращает corpus_id.
    """
    from collections.abc import AsyncIterator

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    blob_store = app_fixture.state.blob_store
    async with factory() as session:
        corpus = Corpus(
            name="t430-corpus",
            workspace_id=workspace_id,
        )
        session.add(corpus)
        await session.flush()

        content = b"This is a test document for embedding. It has enough text for chunking."

        async def gen() -> AsyncIterator[bytes]:
            yield content

        blob_ref = await blob_store.put(gen())

        doc = Document(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            blob_uri=blob_ref.uri,
            filename="test.txt",
            mime="text/plain",
            sha256=blob_ref.sha256,
            source_type="upload",
            status="pending",
        )
        session.add(doc)
        await session.commit()
        return corpus.id


def _mock_embeddings_handler(request: httpx.Request) -> httpx.Response:
    """Отвечает на POST /v1/embeddings в формате OpenAI."""
    import json

    body = json.loads(request.content.decode())
    inputs = body.get("input", [])
    if isinstance(inputs, str):
        inputs = [inputs]
    count = len(inputs)
    return httpx.Response(
        200,
        json={
            "data": [
                {"embedding": [0.1] * 1024, "index": i, "object": "embedding"} for i in range(count)
            ],
            "model": body.get("model", "text-embedding-3-small"),
            "usage": {"prompt_tokens": count, "total_tokens": count},
        },
    )


def _patch_httpx_with_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Перехватывает все httpx.AsyncClient через MockTransport (прецедент: test_oidc_sync)."""
    mock_transport = httpx.MockTransport(_mock_embeddings_handler)

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = mock_transport
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)


@pytest.mark.asyncio
async def test_provider_embedding_backend_through_lifespan(
    provider_api_client: httpx.AsyncClient,
    app_provider_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ProviderEmbeddingBackend через реальный app.state путь.

    app_provider_fixture с embeddings_backend=provider → resolve_embedding_backend
    → ProviderEmbeddingBackend (base_url из БД) → POST /v1/embeddings
    → MockTransport отдаёт векторы → build_index_version собирает индекс.
    """
    await _login_admin(provider_api_client, app_provider_fixture)
    corpus_id = await _seed_corpus_with_document(app_provider_fixture)

    _patch_httpx_with_mock(monkeypatch)

    # Проверяем, что app.state.embedding_backend — реально
    # ProviderEmbeddingBackend, а не AsyncMock (пункт 4).
    backend = app_provider_fixture.state.embedding_backend
    backend_class = type(backend).__name__
    assert backend_class == "ProviderEmbeddingBackend", (
        f"Expected ProviderEmbeddingBackend, got {backend_class} "
        "(app_provider_fixture не подключил provider backend через lifespan-путь)"
    )
    assert backend.model_name() == "text-embedding-3-small"

    # POST build — реально проходит через ProviderEmbeddingBackend.embed()
    # → MockTransport → векторы → индекс собирается.
    resp = await provider_api_client.post(f"/api/corpora/{corpus_id}/index-versions")
    assert resp.status_code == 202
    data = resp.json()
    version_id = data["index_version_id"]

    # Поллим статус до завершения (background task)
    import asyncio

    for _ in range(30):
        await asyncio.sleep(0.2)
        detail = await provider_api_client.get(
            f"/api/corpora/{corpus_id}/index-versions/{version_id}"
        )
        assert detail.status_code == 200
        detail_data = detail.json()
        status = detail_data["status"]
        if status in ("completed", "failed"):
            break
    assert status == "completed", f"Build did not complete: status={status}, data={detail_data}"

    # embedding_model записан из ProviderEmbeddingBackend.model_name()
    assert detail_data["embedding_model"] == "text-embedding-3-small"
    assert detail_data["stats"]["chunks_total"] > 0
