"""Интеграционные тесты eval API (T-224).

POST   /api/corpora/{corpus_id}/eval-sets       — создать набор
GET    /api/corpora/{corpus_id}/eval-sets       — список наборов
GET    /api/eval-sets/{id}                       — набор с элементами
DELETE /api/eval-sets/{id}                       — удалить набор
POST   /api/eval-sets/{id}/import                — импорт CodeSearchNet JSONL
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import Corpus, Role, User, Workspace
from fastapi import FastAPI

FIXTURES = __import__("pathlib").Path(__file__).parent.parent / "fixtures"


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        ws = Workspace(name="test")
        session.add(ws)
        await session.flush()

        role = Role(
            workspace_id=ws.id,
            name="admin",
            policy=BUILTIN_ROLES["admin"].model_dump(),
            is_builtin=True,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=ws.id,
            role_id=role.id,
            email="admin@test.local",
            password_hash=hash_password("test1234"),
            is_active=True,
        )
        session.add(user)
        await session.flush()

        app_fixture.state.workspace_id = ws.id

        cookie = await create_session(session, user.id, ws.id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, cookie)


async def _seed_corpus(app_fixture: FastAPI, name: str = "test-corpus") -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(workspace_id=workspace_id, name=name)
        session.add(corpus)
        await session.commit()
        return corpus.id


# ---------------------------------------------------------------------------
# POST /api/corpora/{corpus_id}/eval-sets
# ---------------------------------------------------------------------------


async def test_create_eval_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Создание набора с элементами."""
    await _login_as_admin(api_client, app_fixture)
    corpus_id = await _seed_corpus(app_fixture)

    response = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={
            "name": "test-set",
            "items": [
                {"question": "Q1", "expected_doc_ids": ["doc-1"], "expected_answer": "A1"},
                {"question": "Q2", "expected_doc_ids": [], "expected_answer": None},
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test-set"
    assert data["corpus_id"] == corpus_id
    assert len(data["items"]) == 2
    assert data["items"][0]["question"] == "Q1"
    assert data["items"][0]["expected_doc_ids"] == ["doc-1"]
    assert data["items"][1]["expected_doc_ids"] == []


async def test_create_eval_set_corpus_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """404 если корпус не существует."""
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.post(
        "/api/corpora/nonexistent/eval-sets",
        json={"name": "test", "items": []},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/corpora/{corpus_id}/eval-sets
# ---------------------------------------------------------------------------


async def test_list_eval_sets(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Список наборов корпуса."""
    await _login_as_admin(api_client, app_fixture)
    corpus_id = await _seed_corpus(app_fixture)

    # Создаём два набора
    for name in ["set-1", "set-2"]:
        await api_client.post(
            f"/api/corpora/{corpus_id}/eval-sets",
            json={"name": name, "items": [{"question": "Q", "expected_doc_ids": []}]},
        )

    response = await api_client.get(f"/api/corpora/{corpus_id}/eval-sets")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    names = {s["name"] for s in data["items"]}
    assert names == {"set-1", "set-2"}


# ---------------------------------------------------------------------------
# GET /api/eval-sets/{id}
# ---------------------------------------------------------------------------


async def test_get_eval_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Получить набор с элементами."""
    await _login_as_admin(api_client, app_fixture)
    corpus_id = await _seed_corpus(app_fixture)

    create_resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={
            "name": "test-set",
            "items": [
                {"question": "Q1", "expected_doc_ids": ["doc-1"], "expected_answer": "A1"},
            ],
        },
    )
    eval_set_id = create_resp.json()["id"]

    response = await api_client.get(f"/api/eval-sets/{eval_set_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == eval_set_id
    assert data["name"] == "test-set"
    assert len(data["items"]) == 1
    assert data["items"][0]["question"] == "Q1"


async def test_get_eval_set_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """404 если набор не существует."""
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.get("/api/eval-sets/nonexistent")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/eval-sets/{id}
# ---------------------------------------------------------------------------


async def test_delete_eval_set(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Удаление набора (каскадно удаляет элементы)."""
    await _login_as_admin(api_client, app_fixture)
    corpus_id = await _seed_corpus(app_fixture)

    create_resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={"name": "to-delete", "items": [{"question": "Q", "expected_doc_ids": []}]},
    )
    eval_set_id = create_resp.json()["id"]

    response = await api_client.delete(f"/api/eval-sets/{eval_set_id}")
    assert response.status_code == 204

    # Проверяем что удалён
    get_resp = await api_client.get(f"/api/eval-sets/{eval_set_id}")
    assert get_resp.status_code == 404


async def test_delete_eval_set_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """404 при удалении несуществующего набора."""
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.delete("/api/eval-sets/nonexistent")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/eval-sets/{id}/import
# ---------------------------------------------------------------------------


async def test_import_codesearchnet(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Импорт CodeSearchNet JSONL через API."""
    await _login_as_admin(api_client, app_fixture)
    corpus_id = await _seed_corpus(app_fixture)

    # Создаём пустой набор
    create_resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={"name": "csn-import", "items": []},
    )
    eval_set_id = create_resp.json()["id"]

    # Загружаем JSONL файл
    fixture_path = FIXTURES / "codesearchnet_sample.jsonl"
    with fixture_path.open("rb") as f:
        response = await api_client.post(
            f"/api/eval-sets/{eval_set_id}/import",
            files={"file": ("codesearchnet.jsonl", f, "application/octet-stream")},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["eval_set_id"] == eval_set_id
    assert data["total_items"] == 3
    assert data["matched_items"] == 0  # Нет документов в корпусе


async def test_import_codesearchnet_with_documents(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Импорт CodeSearchNet с документами в корпусе — matched_items > 0."""
    from app.db.models import Chunk, Document, IndexVersion

    await _login_as_admin(api_client, app_fixture)
    corpus_id = await _seed_corpus(app_fixture, "csn-corpus")

    # Создаём документы и чанки
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        iv = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            embedding_model="BAAI/bge-m3",
            chunker="code",
            chunker_version="1",
            status="active",
        )
        session.add(iv)
        await session.flush()

        doc = Document(
            workspace_id=workspace_id,
            corpus_id=corpus_id,
            blob_uri="sha256-1",
            filename="python/utils.py",
            mime="text/x-python",
            sha256="sha256-1",
            source_type="upload",
            status="indexed",
        )
        session.add(doc)
        await session.flush()

        chunk = Chunk(
            workspace_id=workspace_id,
            index_version_id=iv.id,
            document_id=doc.id,
            ordinal=0,
            text="def parse_json(text):\n    return json.loads(text)",
            meta={},
        )
        session.add(chunk)
        await session.commit()

    # Создаём набор
    create_resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={"name": "csn-with-docs", "items": []},
    )
    eval_set_id = create_resp.json()["id"]

    # Загружаем JSONL
    fixture_path = FIXTURES / "codesearchnet_sample.jsonl"
    with fixture_path.open("rb") as f:
        response = await api_client.post(
            f"/api/eval-sets/{eval_set_id}/import",
            files={"file": ("codesearchnet.jsonl", f, "application/octet-stream")},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["total_items"] == 3
    assert data["matched_items"] == 1  # Только python/utils.py найден

    # Проверяем элементы через GET
    get_resp = await api_client.get(f"/api/eval-sets/{eval_set_id}")
    get_data = get_resp.json()
    # Первый элемент (python/utils.py) — с expected_doc_ids
    assert len(get_data["items"]) == 3
    items_with_docs = [i for i in get_data["items"] if i["expected_doc_ids"]]
    assert len(items_with_docs) == 1


# ---------------------------------------------------------------------------
# T-225: Прогон оценки
# ---------------------------------------------------------------------------


async def _seed_provider_and_model(app_fixture: FastAPI) -> str:
    """Создаёт provider+model, возвращает model alias."""
    from app.auth.passwords import hash_password as _hash  # noqa: F401
    from app.crypto.service import encrypt_api_key
    from app.db.models import Model, Provider

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
            alias="local/test-model",
            upstream_name="test-model",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(model)
        await session.commit()
        return model.alias


async def _seed_corpus_with_index(
    app_fixture: FastAPI, name: str = "eval-corpus"
) -> tuple[str, str]:
    """Создаёт корпус с index_version, возвращает (corpus_id, index_version_id)."""
    from app.db.models import Chunk, Corpus, Document, IndexVersion

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(workspace_id=workspace_id, name=name)
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

        # Создаём документ и чанк
        doc = Document(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            blob_uri="sha256-eval-1",
            filename="doc1.md",
            mime="text/markdown",
            sha256="sha256-eval-1",
            source_type="upload",
            status="indexed",
        )
        session.add(doc)
        await session.flush()

        chunk = Chunk(
            workspace_id=workspace_id,
            index_version_id=iv.id,
            document_id=doc.id,
            ordinal=0,
            text="Content about RAG pipelines",
            meta={"document_filename": "doc1.md", "chunker": "header"},
        )
        session.add(chunk)
        await session.commit()
        return corpus.id, iv.id


def _patch_pipeline_for_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменяет hybrid_search и rerank для eval тестов."""
    from app.rag.hybrid_search import HybridResult, HybridSearchOutput
    from app.rag.reranker import RerankOutput, RerankResult
    from app.rag.vector_store import Hit

    # Заглушка поиска — возвращаем один hit
    async def _stub_hybrid_search(*args: object, **kwargs: object) -> object:
        hits = [Hit(chunk_id="chunk-1", score=1.0, text="Content about RAG")]
        hybrid = [
            HybridResult(chunk_id="chunk-1", score=1.0, text="Content", dense_rank=1, sparse_rank=1)
        ]
        return HybridSearchOutput(dense_hits=hits, sparse_hits=hits, merged=hybrid)

    # Заглушка реранкинга
    async def _stub_rerank(*args: object, **kwargs: object) -> object:
        results = [RerankResult(chunk_id="chunk-1", score=1.0, text="Content", original_rank=1)]
        return RerankOutput(results=results, degraded=False, duration_ms=1.0, error=None)

    monkeypatch.setattr("app.rag.pipeline.hybrid_search", _stub_hybrid_search)
    monkeypatch.setattr("app.rag.pipeline.rerank", _stub_rerank)


async def test_create_eval_run(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запуск прогона через API — возвращает EvalRun с метриками."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    corpus_id, iv_id = await _seed_corpus_with_index(app_fixture)

    # Создаём набор с 2 вопросами
    create_resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={
            "name": "run-test",
            "items": [
                {
                    "question": "What is RAG?",
                    "expected_doc_ids": ["doc-1"],
                    "expected_answer": "RAG is...",
                },
                {
                    "question": "What is MRR?",
                    "expected_doc_ids": [],
                    "expected_answer": "MRR is...",
                },
            ],
        },
    )
    eval_set_id = create_resp.json()["id"]

    _patch_pipeline_for_eval(monkeypatch)

    # Запускаем прогон
    response = await api_client.post(
        f"/api/eval-sets/{eval_set_id}/runs",
        json={"index_version_id": iv_id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["eval_set_id"] == eval_set_id
    assert data["index_version_id"] == iv_id
    assert "metrics" in data
    assert "pipeline" in data
    # pipeline содержит алиас модели
    assert data["pipeline"]["generate_model_alias"] == "local/test-model"
    # Метрики содержат recall@k
    assert "recall@1" in data["metrics"]
    assert "recall@5" in data["metrics"]
    assert "mrr" in data["metrics"]
    assert data["metrics"]["total_items"] == 2


async def test_list_eval_runs(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Список прогонов набора."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    corpus_id, iv_id = await _seed_corpus_with_index(app_fixture)

    create_resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={"name": "list-runs-test", "items": [{"question": "Q", "expected_doc_ids": []}]},
    )
    eval_set_id = create_resp.json()["id"]

    _patch_pipeline_for_eval(monkeypatch)

    # Запускаем прогон
    await api_client.post(
        f"/api/eval-sets/{eval_set_id}/runs",
        json={"index_version_id": iv_id},
    )

    response = await api_client.get(f"/api/eval-sets/{eval_set_id}/runs")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["eval_set_id"] == eval_set_id


async def test_get_eval_run(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Получение прогона по ID."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    corpus_id, iv_id = await _seed_corpus_with_index(app_fixture)

    create_resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={"name": "get-run-test", "items": [{"question": "Q", "expected_doc_ids": []}]},
    )
    eval_set_id = create_resp.json()["id"]

    _patch_pipeline_for_eval(monkeypatch)

    run_resp = await api_client.post(
        f"/api/eval-sets/{eval_set_id}/runs",
        json={"index_version_id": iv_id},
    )
    run_id = run_resp.json()["id"]

    response = await api_client.get(f"/api/eval-runs/{run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == run_id
    assert "metrics" in data


async def test_create_eval_run_set_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """404 если набор не существует."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    _, iv_id = await _seed_corpus_with_index(app_fixture)

    response = await api_client.post(
        "/api/eval-sets/nonexistent/runs",
        json={"index_version_id": iv_id},
    )
    assert response.status_code == 404


async def test_create_eval_run_index_not_found(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """404 если index_version не существует."""
    await _login_as_admin(api_client, app_fixture)
    await _seed_provider_and_model(app_fixture)
    corpus_id, _ = await _seed_corpus_with_index(app_fixture)

    create_resp = await api_client.post(
        f"/api/corpora/{corpus_id}/eval-sets",
        json={"name": "bad-iv-test", "items": [{"question": "Q", "expected_doc_ids": []}]},
    )
    eval_set_id = create_resp.json()["id"]

    response = await api_client.post(
        f"/api/eval-sets/{eval_set_id}/runs",
        json={"index_version_id": "nonexistent"},
    )
    assert response.status_code == 404
