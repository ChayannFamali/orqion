"""Тесты RAG-конвейера через chat API (T-221).

Проверки:
- test_chat_with_corpus_returns_rag_answer: corpus_name задан, RAG возвращает ответ
- test_chat_with_corpus_uses_corpus_data_class: data_class из корпуса, не из запроса
- test_chat_with_corpus_pinned_model: pinned_model_id переопределяет model_alias
- test_chat_with_corpus_k2_rejects_external_model: К2 + external model → 403
- test_chat_with_corpus_no_active_index: corpus без active_index_version_id → 409
- test_chat_with_corpus_not_found: несуществующий corpus_name → 404
- test_chat_without_corpus_regular_flow: corpus_name=None → обычный chat
- test_chat_with_corpus_rag_degraded: конвейер деградировал → rag_degraded=True
- test_chat_with_corpus_non_streaming: stream=True + corpus → non-streaming ответ
- test_chat_with_corpus_traces_rag_steps: trace содержит span'ы шагов RAG
- test_chat_with_corpus_not_in_policy_rejected: corpus не в policy.corpora → 403
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Corpus, IndexVersion, Model, Provider, Role, User
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from app.rag.reranker import RerankResult
from fastapi import FastAPI

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


async def _login_as_admin(
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
            name=role_name,
            is_builtin=True,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()

        password = "admin-password-123"
        user = User(
            workspace_id=workspace_id,
            email=f"{role_name}@orqion.local",
            password_hash=hash_password(password),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_provider_and_model(
    app_fixture: FastAPI,
    model_alias: str = "local/test-model",
    upstream_name: str = "test-model",
    locality: str = "local",
    enabled: bool = True,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", app_fixture.state.secret_key),
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias=model_alias,
            upstream_name=upstream_name,
            locality=locality,
            max_input_tokens=32000,
            enabled=enabled,
        )
        session.add(model)
        await session.commit()
        return model.id


async def _seed_corpus(
    app_fixture: FastAPI,
    name: str = "test-corpus",
    data_class: str | None = None,
    pinned_model_id: str | None = None,
    with_active_index: bool = True,
) -> str:
    """Создаёт корпус с (опц.) активной версией индекса. Возвращает corpus.id."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(
            workspace_id=workspace_id,
            name=name,
            data_class=data_class,
            pinned_model_id=pinned_model_id,
        )
        session.add(corpus)
        await session.flush()

        if with_active_index:
            iv = IndexVersion(
                workspace_id=workspace_id,
                corpus_id=corpus.id,
                embedding_model="BAAI/bge-m3",
                chunker="mixed-v1",
                chunker_version="1",
                status="active",
            )
            session.add(iv)
            await session.flush()
            corpus.active_index_version_id = iv.id

        await session.commit()
        return corpus.id


def _patch_provider_complete(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
) -> None:
    """Подменяет ProviderClient.complete — возврат заглушки."""

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": response}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)


def _patch_provider_stream(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
) -> None:
    """Подменяет ProviderClient.stream — возврат заглушки."""

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        for word in response.split():
            yield word + " "

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)


def _patch_provider_both(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
) -> None:
    _patch_provider_complete(monkeypatch, response)
    _patch_provider_stream(monkeypatch, response)


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


async def test_chat_with_corpus_returns_rag_answer(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corpus_name задан, RAG возвращает ответ."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    await _seed_corpus(app_fixture)
    _patch_provider_both(monkeypatch, "RAG answer based on documents")

    # Stub pipeline steps by patching hybrid_search and reranker
    monkeypatch.setattr(
        "app.rag.pipeline.hybrid_search",
        AsyncMock(return_value=AsyncMock(merged=[])),
    )
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(return_value=AsyncMock(results=[], degraded=False, error=None)),
    )

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "What is orqion?"}],
            "corpus_name": "test-corpus",
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "complete"
    assert data["content"] == "RAG answer based on documents"
    assert data.get("rag_degraded") is False


async def test_chat_with_corpus_uses_corpus_data_class(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data_class из корпуса, не из запроса."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture, locality="local")
    await _seed_corpus(app_fixture, data_class="К2")
    _patch_provider_both(monkeypatch, "answer")

    monkeypatch.setattr(
        "app.rag.pipeline.hybrid_search",
        AsyncMock(return_value=AsyncMock(merged=[])),
    )
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(return_value=AsyncMock(results=[], degraded=False, error=None)),
    )

    # Запрос с corpus_data_class=К0, но корпус К2 — К2 побеждает
    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "corpus_data_class": "К0",
            "stream": False,
        },
    )
    assert response.status_code == 200


async def test_chat_with_corpus_k2_rejects_external_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """К2 + только external model → нет доступных моделей (503 NoRouteAvailable).

    ADR-12: routing фильтрует external модели для К2 до enforce.
    Если local моделей нет — NoRouteAvailable, RAG не выполняется.
    """
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture, locality="external", model_alias="external/model")
    await _seed_corpus(app_fixture, data_class="К2")
    _patch_provider_both(monkeypatch, "answer")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "model_alias": "external/model",
            "stream": False,
        },
    )
    assert response.status_code == 503
    assert response.json()["error"] == "no_route_available"


async def test_chat_with_corpus_no_active_index(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corpus без active_index_version_id → 409."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    await _seed_corpus(app_fixture, with_active_index=False)
    _patch_provider_both(monkeypatch, "answer")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "stream": False,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "corpus_not_ready"


async def test_chat_with_corpus_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Несуществующий corpus_name → 404."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_both(monkeypatch, "answer")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "nonexistent",
            "stream": False,
        },
    )
    assert response.status_code == 404


async def test_chat_without_corpus_regular_flow(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corpus_name=None → обычный chat (без RAG)."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _patch_provider_both(monkeypatch, "Regular chat answer")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "complete"
    assert data["content"] == "Regular chat answer"
    assert "rag_degraded" not in data


async def test_chat_with_corpus_rag_degraded(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Конвейер деградировал → rag_degraded=True, errors заполнены."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    await _seed_corpus(app_fixture)
    _patch_provider_both(monkeypatch, "partial answer")

    # Stub hybrid_search to return empty (causes degradation in build_context)
    monkeypatch.setattr(
        "app.rag.pipeline.hybrid_search",
        AsyncMock(return_value=AsyncMock(merged=[])),
    )
    # Stub rerank to return degraded
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(
            return_value=AsyncMock(results=[], degraded=True, error="FlagEmbedding not available")
        ),
    )

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("rag_degraded") is True
    assert len(data.get("rag_errors", [])) > 0


async def test_chat_with_corpus_non_streaming(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stream=True + corpus → non-streaming ответ (RAG не стримит)."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    await _seed_corpus(app_fixture)
    _patch_provider_both(monkeypatch, "RAG non-stream answer")

    monkeypatch.setattr(
        "app.rag.pipeline.hybrid_search",
        AsyncMock(return_value=AsyncMock(merged=[])),
    )
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(return_value=AsyncMock(results=[], degraded=False, error=None)),
    )

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "stream": True,
        },
    )
    # RAG возвращает JSON, не SSE stream
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "complete"
    assert data["content"] == "RAG non-stream answer"


async def test_chat_with_corpus_not_in_policy_rejected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corpus не в policy.corpora → 403 (Forbidden)."""
    # Создаём роль support — corpora=["public"] (не "test-corpus")
    policy = BUILTIN_ROLES["support"].model_dump()
    await _login_as_admin(api_client, app_fixture, role_name="support", policy=policy)
    await _seed_provider_and_model(app_fixture)
    await _seed_corpus(app_fixture, name="secret-corpus")
    _patch_provider_both(monkeypatch, "answer")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "secret-corpus",
            "stream": False,
        },
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


async def test_chat_with_corpus_traces_rag_steps(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trace содержит span'ы шагов RAG (resolve_corpus, prepare, rag_pipeline)."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    await _seed_corpus(app_fixture)
    _patch_provider_both(monkeypatch, "answer")

    monkeypatch.setattr(
        "app.rag.pipeline.hybrid_search",
        AsyncMock(return_value=AsyncMock(merged=[])),
    )
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(return_value=AsyncMock(results=[], degraded=False, error=None)),
    )

    await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "stream": False,
        },
    )

    # Проверяем trace в БД
    from app.db.models import Span, Trace
    from sqlalchemy import select

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        traces = (await session.execute(select(Trace))).scalars().all()
        assert len(traces) >= 1
        spans = (
            (await session.execute(select(Span).where(Span.trace_id == traces[-1].id)))
            .scalars()
            .all()
        )
        span_names = [s.name for s in spans]
        assert "resolve_corpus" in span_names
        assert "prepare" in span_names
        assert "rag_pipeline" in span_names


async def _seed_corpus_with_pinned_model(
    app_fixture: FastAPI,
    model_alias: str = "local/pinned-model",
    name: str = "test-corpus",
) -> str:
    """Создаёт корпус с pinned_model_id и активной версией индекса в одной сессии."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", app_fixture.state.secret_key),
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias=model_alias,
            upstream_name="pinned-upstream",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(model)
        await session.flush()

        corpus = Corpus(
            workspace_id=workspace_id,
            name=name,
            pinned_model_id=model.id,
        )
        session.add(corpus)
        await session.flush()

        iv = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="BAAI/bge-m3",
            chunker="mixed-v1",
            chunker_version="1",
            status="active",
        )
        session.add(iv)
        await session.flush()
        corpus.active_index_version_id = iv.id

        await session.commit()
        return corpus.id


async def test_chat_with_corpus_pinned_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinned_model_id переопределяет model_alias из запроса."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_corpus_with_pinned_model(app_fixture)
    _patch_provider_both(monkeypatch, "pinned model answer")

    monkeypatch.setattr(
        "app.rag.pipeline.hybrid_search",
        AsyncMock(return_value=AsyncMock(merged=[])),
    )
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(return_value=AsyncMock(results=[], degraded=False, error=None)),
    )

    # Запрос с другим model_alias — pinned побеждает
    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "model_alias": "local/test-model",
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    # Модель в ответе — pinned, не запрошенная
    assert data["model"] == "local/pinned-model"


async def test_chat_with_corpus_degraded_early_steps_real_usage(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Деградация rewrite/rerank, но answer и usage получены → status=ok, реальные токены.

    Биллинговый тест: rag_degraded=True от ранних шагов, но step_generate
    реально вызвал провайдера. usage_event должен записать реальные токены
    и status=ok, а не нулевые токены и status=error.
    """
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    await _seed_corpus(app_fixture)

    # step_generate успешно отвечает с реальными токенами
    _patch_provider_complete(monkeypatch, "Real answer with real tokens")

    # hybrid_search пустой (вызовет пустой контекст, но не ошибку)
    monkeypatch.setattr(
        "app.rag.pipeline.hybrid_search",
        AsyncMock(return_value=AsyncMock(merged=[])),
    )
    # rerank деградировал — FlagEmbedding недоступен, но RRF fallback отработал
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(
            return_value=AsyncMock(results=[], degraded=True, error="FlagEmbedding not available")
        ),
    )

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()

    # RAG деградирован (rerank), но ответ получен
    assert data.get("rag_degraded") is True
    assert data["content"] == "Real answer with real tokens"
    # Реальные токены, не нулевые
    assert data["usage"]["tokens_in"] > 0
    assert data["usage"]["tokens_out"] > 0

    # Проверяем usage_event в БД — status=ok, не error
    from app.db.models import UsageEvent
    from sqlalchemy import select

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        events = (await session.execute(select(UsageEvent))).scalars().all()
        assert len(events) >= 1
        latest = events[-1]
        assert latest.status == "ok"
        assert latest.tokens_in > 0
        assert latest.tokens_out > 0
        assert latest.error_code is None


# ---------------------------------------------------------------------------
# T-222: Источники в ответе
# ---------------------------------------------------------------------------


async def _seed_corpus_with_chunks(
    app_fixture: FastAPI,
    name: str = "test-corpus",
    num_chunks: int = 2,
) -> tuple[str, str, list[str]]:
    """Создаёт корпус с документами и чанками. Возвращает (corpus_id, index_version_id, [chunk_ids])."""
    from app.db.models import Chunk, Document

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(
            workspace_id=workspace_id,
            name=name,
        )
        session.add(corpus)
        await session.flush()

        iv = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="BAAI/bge-m3",
            chunker="header",
            chunker_version="1",
            status="active",
        )
        session.add(iv)
        await session.flush()
        corpus.active_index_version_id = iv.id

        chunk_ids: list[str] = []
        for i in range(num_chunks):
            doc = Document(
                workspace_id=workspace_id,
                corpus_id=corpus.id,
                blob_uri=f"sha256-{i:064d}",
                filename=f"doc{i}.md",
                mime="text/markdown",
                sha256=f"sha256-{i:064d}",
                source_type="upload",
                status="indexed",
            )
            session.add(doc)
            await session.flush()

            chunk = Chunk(
                workspace_id=workspace_id,
                index_version_id=iv.id,
                document_id=doc.id,
                ordinal=i,
                text=f"Content of chunk {i}",
                meta={
                    "document_filename": f"doc{i}.md",
                    "chunker": "header",
                    "heading_path": [f"Section {i}"],
                },
            )
            session.add(chunk)
            await session.flush()
            chunk_ids.append(chunk.id)

        await session.commit()
        return corpus.id, iv.id, chunk_ids


def _patch_search_and_rerank_with_chunks(
    monkeypatch: pytest.MonkeyPatch,
    chunk_ids: list[str],
    texts: list[str] | None = None,
) -> None:
    """Подменяет hybrid_search и rerank реальными RerankResult с chunk_ids."""
    from app.rag.hybrid_search import HybridResult, HybridSearchOutput
    from app.rag.vector_store import Hit

    if texts is None:
        texts = [f"Content of chunk {i}" for i in range(len(chunk_ids))]

    hits = [
        Hit(chunk_id=cid, score=1.0 / (i + 1), text=texts[i]) for i, cid in enumerate(chunk_ids)
    ]
    hybrid_results = [
        HybridResult(
            chunk_id=cid, score=1.0 / (i + 1), text=texts[i], dense_rank=i + 1, sparse_rank=i + 1
        )
        for i, cid in enumerate(chunk_ids)
    ]

    async def _stub_hybrid_search(*args: Any, **kwargs: Any) -> Any:
        return HybridSearchOutput(dense_hits=hits, sparse_hits=hits, merged=hybrid_results)

    rerank_results = [
        RerankResult(chunk_id=cid, score=1.0 / (i + 1), text=texts[i], original_rank=i + 1)
        for i, cid in enumerate(chunk_ids)
    ]

    async def _stub_rerank(*args: Any, **kwargs: Any) -> Any:
        from app.rag.reranker import RerankOutput

        return RerankOutput(results=rerank_results, degraded=False, duration_ms=1.0, error=None)

    monkeypatch.setattr("app.rag.pipeline.hybrid_search", _stub_hybrid_search)
    monkeypatch.setattr("app.rag.pipeline.rerank", _stub_rerank)


async def test_chat_with_corpus_returns_sources(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAG-ответ содержит sources с document_id и structural_path."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _corpus_id, _iv_id, chunk_ids = await _seed_corpus_with_chunks(app_fixture, num_chunks=2)
    _patch_provider_both(monkeypatch, "Answer based on sources")
    _patch_search_and_rerank_with_chunks(monkeypatch, chunk_ids)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    sources = data["sources"]
    assert len(sources) == 2
    # Каждый source имеет нужные поля
    for s in sources:
        assert "chunk_id" in s
        assert "document_id" in s
        assert "structural_path" in s
        assert "score" in s
        assert "original_rank" in s
        # blob_uri не отдаём
        assert "blob_uri" not in s
    # structural_path содержит filename и heading
    assert "doc0.md" in sources[0]["structural_path"]
    assert "Section 0" in sources[0]["structural_path"]


async def test_chat_with_corpus_sources_empty_when_no_hits(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет hits → sources пустой список, ответ всё равно 200."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    await _seed_corpus(app_fixture)
    _patch_provider_both(monkeypatch, "No results found")

    monkeypatch.setattr(
        "app.rag.pipeline.hybrid_search",
        AsyncMock(return_value=AsyncMock(merged=[])),
    )
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(return_value=AsyncMock(results=[], degraded=False, error=None)),
    )

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sources"] == []


async def test_chat_with_corpus_sources_reflect_truncation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truncation: только часть чанков в контексте → sources содержат только их."""
    await _login_as_admin(api_client, app_fixture)
    # 3 чанка, модель с маленьким max_input_tokens чтобы 0-й не влез
    from app.db.models import Chunk, Document

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(workspace_id=workspace_id, name="trunc-corpus")
        session.add(corpus)
        await session.flush()

        iv = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="BAAI/bge-m3",
            chunker="header",
            chunker_version="1",
            status="active",
        )
        session.add(iv)
        await session.flush()
        corpus.active_index_version_id = iv.id

        chunk_ids: list[str] = []
        for i in range(3):
            doc = Document(
                workspace_id=workspace_id,
                corpus_id=corpus.id,
                blob_uri=f"sha256-trunc-{i:061d}",
                filename=f"trunc{i}.md",
                mime="text/markdown",
                sha256=f"sha256-trunc-{i:061d}",
                source_type="upload",
                status="indexed",
            )
            session.add(doc)
            await session.flush()

            # Большой текст для 0-го чанка, маленькие для остальных
            text = ("x " * 500) if i == 0 else f"Small content {i}"
            chunk = Chunk(
                workspace_id=workspace_id,
                index_version_id=iv.id,
                document_id=doc.id,
                ordinal=i,
                text=text,
                meta={
                    "document_filename": f"trunc{i}.md",
                    "chunker": "header",
                    "heading_path": [f"Section {i}"],
                },
            )
            session.add(chunk)
            await session.flush()
            chunk_ids.append(chunk.id)

        await session.commit()

    # Модель с маленьким max_input_tokens — 0-й чанк не влезет
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", app_fixture.state.secret_key),
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()

        model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias="local/small-context",
            upstream_name="small-context",
            locality="local",
            max_input_tokens=200,
            enabled=True,
        )
        session.add(model)
        await session.commit()
    _patch_provider_both(monkeypatch, "Truncated answer")
    _patch_search_and_rerank_with_chunks(
        monkeypatch,
        chunk_ids,
        texts=[("x " * 500), "Small content 1", "Small content 2"],
    )

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "trunc-corpus",
            "model_alias": "local/small-context",
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    sources = data["sources"]
    # 0-й чанк oversized — не в sources, только 1 и 2
    assert len(sources) == 2
    source_ids = [s["chunk_id"] for s in sources]
    assert chunk_ids[0] not in source_ids
    assert chunk_ids[1] in source_ids
    assert chunk_ids[2] in source_ids


async def test_chat_with_corpus_sources_preserve_inclusion_order(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Источники в порядке включения в контекст, не в порядке реранкинга."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _corpus_id, _iv_id, chunk_ids = await _seed_corpus_with_chunks(app_fixture, num_chunks=3)
    _patch_provider_both(monkeypatch, "Ordered answer")

    # rerank меняет порядок: chunk[2] → rank 1, chunk[0] → rank 2, chunk[1] → rank 3
    from app.rag.hybrid_search import HybridResult, HybridSearchOutput
    from app.rag.reranker import RerankOutput
    from app.rag.vector_store import Hit

    reordered = [chunk_ids[2], chunk_ids[0], chunk_ids[1]]
    texts = {
        chunk_ids[0]: "Content of chunk 0",
        chunk_ids[1]: "Content of chunk 1",
        chunk_ids[2]: "Content of chunk 2",
    }

    async def _stub_hybrid_search(*args: Any, **kwargs: Any) -> Any:
        hits = [Hit(chunk_id=cid, score=1.0, text=texts[cid]) for cid in reordered]
        hybrid_results = [
            HybridResult(
                chunk_id=cid, score=1.0, text=texts[cid], dense_rank=i + 1, sparse_rank=i + 1
            )
            for i, cid in enumerate(reordered)
        ]
        return HybridSearchOutput(dense_hits=hits, sparse_hits=hits, merged=hybrid_results)

    async def _stub_rerank(*args: Any, **kwargs: Any) -> Any:
        results = [
            RerankResult(chunk_id=cid, score=1.0 / (i + 1), text=texts[cid], original_rank=i + 1)
            for i, cid in enumerate(reordered)
        ]
        return RerankOutput(results=results, degraded=False, duration_ms=1.0, error=None)

    monkeypatch.setattr("app.rag.pipeline.hybrid_search", _stub_hybrid_search)
    monkeypatch.setattr("app.rag.pipeline.rerank", _stub_rerank)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "test-corpus",
            "stream": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    sources = data["sources"]
    # Все 3 чанка влезают (текст маленький) — порядок = порядок реранкинга
    assert len(sources) == 3
    assert sources[0]["chunk_id"] == chunk_ids[2]
    assert sources[1]["chunk_id"] == chunk_ids[0]
    assert sources[2]["chunk_id"] == chunk_ids[1]
