"""T-439: мульти-корпусный RAG-запрос (решения дизайн-ревью зафиксированы в планинге).

Проверки:
- два корпуса: поиск по обоим, источники атрибутированы корпусами;
- любой неразрешённый корпус → 403 со списком непройденных (Б1);
- оба поля (corpus_name + corpus_names) → 400 (Г1);
- конфликт пинов → 400 pin_conflict (Д1); общий пин применяется;
- строжайший data_class побеждает: К0+К2 + только external → 503 (А1);
- неготовый корпус в списке → 409, весь запрос падает (Е1);
- ненайденный корпус в списке → 404;
- пустой список = обычный чат;
- GET /api/corpora/available фильтруется по policy.corpora.
"""

from __future__ import annotations

from typing import Any

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


async def _login(
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
        user = User(
            workspace_id=workspace_id,
            email=f"{role_name}@orqion.local",
            password_hash=hash_password("password-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_model(
    app_fixture: FastAPI,
    model_alias: str = "local/test-model",
    locality: str = "local",
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
            upstream_name=model_alias.split("/")[-1],
            locality=locality,
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(model)
        await session.commit()
        return model.id


async def _seed_corpus_with_chunks(
    app_fixture: FastAPI,
    name: str,
    num_chunks: int = 2,
    data_class: str | None = None,
    pinned_model_id: str | None = None,
    with_active_index: bool = True,
) -> tuple[str, str | None, list[str]]:
    """Корпус с документами/чанками. Возвращает (corpus_id, iv_id, chunk_ids)."""
    from app.db.models import Chunk, Document

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

        chunk_ids: list[str] = []
        iv_id: str | None = None
        if with_active_index:
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
            iv_id = iv.id

            for i in range(num_chunks):
                doc = Document(
                    workspace_id=workspace_id,
                    corpus_id=corpus.id,
                    blob_uri=f"{name}-{i:060d}",
                    filename=f"{name}-doc{i}.md",
                    mime="text/markdown",
                    sha256=f"{name}-{i:060d}",
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
                    text=f"{name} chunk {i}",
                    meta={
                        "document_filename": f"{name}-doc{i}.md",
                        "chunker": "header",
                        "heading_path": [f"Section {i}"],
                    },
                )
                session.add(chunk)
                await session.flush()
                chunk_ids.append(chunk.id)

        await session.commit()
        return corpus.id, iv_id, chunk_ids


def _patch_provider(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
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


def _patch_search_by_version(
    monkeypatch: pytest.MonkeyPatch,
    chunks_by_version: dict[str, list[str]],
) -> None:
    """Гибридный поиск возвращает чанки своей версии индекса (по аргументу).

    Имитирует реальный поиск: каждый корпус отдаёт только свои чанки.
    """
    from app.rag.hybrid_search import HybridResult, HybridSearchOutput
    from app.rag.vector_store import Hit

    async def _stub_hybrid_search(
        vector_store: Any,
        embedding_backend: Any,
        index_version_id: str,
        query: str,
        k: int = 50,
    ) -> HybridSearchOutput:
        chunk_ids = chunks_by_version.get(index_version_id, [])
        hits = [
            Hit(chunk_id=cid, score=1.0 / (i + 1), text=f"text-{cid}")
            for i, cid in enumerate(chunk_ids)
        ]
        merged = [
            HybridResult(
                chunk_id=cid,
                score=1.0 / (i + 1),
                text=f"text-{cid}",
                dense_rank=i + 1,
                sparse_rank=i + 1,
            )
            for i, cid in enumerate(chunk_ids)
        ]
        return HybridSearchOutput(dense_hits=hits, sparse_hits=hits, merged=merged)

    all_ids = [cid for ids in chunks_by_version.values() for cid in ids]

    async def _stub_rerank(*args: Any, **kwargs: Any) -> Any:
        from app.rag.reranker import RerankOutput

        results = [
            RerankResult(chunk_id=cid, score=1.0 / (i + 1), text=f"text-{cid}", original_rank=i + 1)
            for i, cid in enumerate(all_ids)
        ]
        return RerankOutput(results=results, degraded=False, duration_ms=1.0, error=None)

    monkeypatch.setattr("app.rag.pipeline.hybrid_search", _stub_hybrid_search)
    monkeypatch.setattr("app.rag.pipeline.rerank", _stub_rerank)


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_corpus_searches_both_and_attributes_sources(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Два корпуса: поиск по обоим, источники атрибутированы корпусами."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)
    _c1, iv1, chunks1 = await _seed_corpus_with_chunks(app_fixture, "corpus-a")
    _c2, iv2, chunks2 = await _seed_corpus_with_chunks(app_fixture, "corpus-b")
    assert iv1 is not None and iv2 is not None
    _patch_provider(monkeypatch, "multi answer")
    _patch_search_by_version(monkeypatch, {iv1: chunks1, iv2: chunks2})

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_names": ["corpus-a", "corpus-b"],
            "stream": False,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["content"] == "multi answer"
    # Источники из обоих корпусов, каждый со своей атрибуцией
    sources = data["sources"]
    assert len(sources) == len(chunks1) + len(chunks2)
    by_chunk = {s["chunk_id"]: s for s in sources}
    for cid in chunks1:
        assert by_chunk[cid]["corpus_name"] == "corpus-a"
        assert by_chunk[cid]["corpus_id"] == _c1
    for cid in chunks2:
        assert by_chunk[cid]["corpus_name"] == "corpus-b"
        assert by_chunk[cid]["corpus_id"] == _c2


@pytest.mark.asyncio
async def test_multi_corpus_any_forbidden_returns_403_with_list(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Б1: любой неразрешённый корпус → 403 со списком непройденных."""
    policy = BUILTIN_ROLES["support"].model_dump()  # corpora=["public"]
    await _login(api_client, app_fixture, role_name="support", policy=policy)
    await _seed_model(app_fixture)
    await _seed_corpus_with_chunks(app_fixture, "public")
    await _seed_corpus_with_chunks(app_fixture, "internal")
    _patch_provider(monkeypatch, "answer")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_names": ["public", "internal"],
            "stream": False,
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "forbidden"
    assert body["constraint"]["corpora"] == ["internal"]


@pytest.mark.asyncio
async def test_multi_corpus_both_fields_rejected_400(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Г1: corpus_name и corpus_names одновременно → 400."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_name": "corpus-a",
            "corpus_names": ["corpus-a"],
            "stream": False,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


@pytest.mark.asyncio
async def test_multi_corpus_pin_conflict_400(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Д1: два корпуса с разными пинами → 400, конфликт закрепления."""
    await _login(api_client, app_fixture)
    pin1 = await _seed_model(app_fixture, model_alias="local/model-one")
    pin2 = await _seed_model(app_fixture, model_alias="local/model-two")
    await _seed_corpus_with_chunks(app_fixture, "corpus-a", pinned_model_id=pin1)
    await _seed_corpus_with_chunks(app_fixture, "corpus-b", pinned_model_id=pin2)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_names": ["corpus-a", "corpus-b"],
            "stream": False,
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_request"
    assert body["constraint"]["reason"] == "pin_conflict"


@pytest.mark.asyncio
async def test_multi_corpus_shared_pin_applied(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Д1: один общий пин на корпуса применяется — модель в ответе == пин."""
    await _login(api_client, app_fixture)
    pin = await _seed_model(app_fixture, model_alias="local/pinned-model")
    # Дополнительный кандидат: без фикса пин мог бы потеряться среди кандидатов
    await _seed_model(app_fixture, model_alias="local/aaa-other")
    await _seed_corpus_with_chunks(app_fixture, "corpus-a", pinned_model_id=pin)
    await _seed_corpus_with_chunks(app_fixture, "corpus-b", pinned_model_id=pin)
    _patch_provider(monkeypatch, "pinned answer")
    monkeypatch.setattr(
        "app.rag.pipeline.hybrid_search",
        _empty_hybrid_search(),
    )
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        _empty_rerank(),
    )

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_names": ["corpus-a", "corpus-b"],
            "model_alias": "local/aaa-other",
            "stream": False,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["model"] == "local/pinned-model"


@pytest.mark.asyncio
async def test_multi_corpus_strictest_data_class_locks_local(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """А1: К0 + К2 → весь запрос только на локальных; только external → 503."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture, model_alias="external/model", locality="external")
    await _seed_corpus_with_chunks(app_fixture, "open-corpus", data_class="К0")
    await _seed_corpus_with_chunks(app_fixture, "secret-corpus", data_class="К2")
    _patch_provider(monkeypatch, "answer")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_names": ["open-corpus", "secret-corpus"],
            "model_alias": "external/model",
            "stream": False,
        },
    )
    assert response.status_code == 503
    assert response.json()["error"] == "no_route_available"


@pytest.mark.asyncio
async def test_multi_corpus_one_not_ready_fails_whole_request(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Е1: неготовый корпус в списке → 409, весь запрос падает (fail-closed)."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)
    await _seed_corpus_with_chunks(app_fixture, "ready-corpus")
    await _seed_corpus_with_chunks(app_fixture, "not-ready-corpus", with_active_index=False)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_names": ["ready-corpus", "not-ready-corpus"],
            "stream": False,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "corpus_not_ready"


@pytest.mark.asyncio
async def test_multi_corpus_one_not_found_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Ненайденный корпус в списке → 404."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)
    await _seed_corpus_with_chunks(app_fixture, "real-corpus")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_names": ["real-corpus", "ghost-corpus"],
            "stream": False,
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_multi_corpus_empty_list_is_plain_chat(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пустой список корпусов = обычный чат без RAG."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)
    _patch_provider(monkeypatch, "plain answer")

    from app.providers.client import ProviderClient

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        yield {"type": "token", "v": "plain answer"}

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "query"}],
            "corpus_names": [],
            "stream": False,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["content"] == "plain answer"
    assert data["sources"] == []


@pytest.mark.asyncio
async def test_available_corpora_filtered_by_policy(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """GET /api/corpora/available — только корпуса из policy.corpora роли."""
    await _seed_model(app_fixture)
    await _seed_corpus_with_chunks(app_fixture, "public")
    await _seed_corpus_with_chunks(app_fixture, "internal")
    await _seed_corpus_with_chunks(app_fixture, "draft", with_active_index=False)

    # admin: «*» — видит все, включая неготовый (ready=False)
    await _login(api_client, app_fixture)
    response = await api_client.get("/api/corpora/available")
    assert response.status_code == 200
    entries = {c["name"]: c for c in response.json()["corpora"]}
    assert set(entries) == {"public", "internal", "draft"}
    assert entries["public"]["ready"] is True
    assert entries["draft"]["ready"] is False

    # support: corpora=["public"] — видит только его
    await _login(
        api_client, app_fixture, role_name="support", policy=BUILTIN_ROLES["support"].model_dump()
    )
    response = await api_client.get("/api/corpora/available")
    assert response.status_code == 200
    names = [c["name"] for c in response.json()["corpora"]]
    assert names == ["public"]


def _empty_hybrid_search() -> Any:
    from app.rag.hybrid_search import HybridSearchOutput

    async def _stub(*args: Any, **kwargs: Any) -> HybridSearchOutput:
        return HybridSearchOutput(dense_hits=[], sparse_hits=[], merged=[])

    return _stub


def _empty_rerank() -> Any:
    from app.rag.reranker import RerankOutput

    async def _stub(*args: Any, **kwargs: Any) -> RerankOutput:
        return RerankOutput(results=[], degraded=False, duration_ms=1.0, error=None)

    return _stub
