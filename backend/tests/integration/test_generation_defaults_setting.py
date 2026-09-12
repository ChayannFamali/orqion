"""Температура генерации по умолчанию как настройка рабочей области.

Проверяется поведение, а не наличие записей: значение, разрешённое из
реестра, доходит до фактического параметра запроса к провайдеру по всем
трём путям, которыми собирается ответ, — обычный чат, генерация по
документам (RAG) и агентный прогон. Смена настройки через API действует
без перезапуска приложения, явное значение в запросе чата побеждает,
нерабочее значение не сохраняется, а роль без права на запись его не видит.

Провайдер подменяется заглушкой, перехватывающей ``temperature`` каждого
вызова; обращения к сети запрещены.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import (
    Chunk,
    Corpus,
    Document,
    IndexVersion,
    Model,
    Provider,
    Role,
    User,
)
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from app.rag.reranker import RerankResult
from app.settings.registry import (
    DEFAULT_TEMPERATURE_KEY,
    GENERATION_CATEGORY,
    TEMPERATURE_MAX,
)
from fastapi import FastAPI

SETTINGS_PATH = "/api/workspace/settings"
CHAT_PATH = "/api/chat"
AGENT_PATH = "/api/agent/chat"
LOGIN_PATH = "/api/auth/login"

#: Env-дефолт проверок: маршруты читают ambient ``Settings()`` на каждый
#: запрос, поэтому переменная окружения задаёт исходную точку
#: детерминированно и отличается от заводских 0.7.
ENV_TEMPERATURE = 0.55


@pytest.fixture(autouse=True)
def _env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORQION_DEFAULT_TEMPERATURE", str(ENV_TEMPERATURE))


# ---------------------------------------------------------------------------
# Хелперы: вход, модель, корпус
# ---------------------------------------------------------------------------


async def _login(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    *,
    role_name: str = "admin",
    email: str | None = None,
) -> str:
    """Пользователь builtin-роли; возвращает user_id и ставит-cookie сессию."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    password = f"gen-{role_name}-pass-123"
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name=f"gen-{role_name}-{email or role_name}",
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email=email or f"gen-{role_name}@orqion.local",
            password_hash=hash_password(password),
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_model(
    app_fixture: FastAPI,
    *,
    alias: str = "local/gen-model",
    supports_tools: bool = False,
) -> str:
    """Провайдер и модель; возвращает model_id."""
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
            alias=alias,
            upstream_name=alias.split("/")[-1],
            locality="local",
            max_input_tokens=32000,
            enabled=True,
            supports_tools=supports_tools,
        )
        session.add(model)
        await session.commit()
        return model.id


async def _seed_corpus_with_chunks(
    app_fixture: FastAPI,
    name: str,
    num_chunks: int = 2,
) -> tuple[str, str, list[str]]:
    """Корпус с активной версией индекса, документами и чанками.

    Возвращает (corpus_id, index_version_id, chunk_ids). Векторы не нужны:
    поиск подменяется заглушкой, возвращающей эти чанки.
    """
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(workspace_id=workspace_id, name=name, data_class="К0")
        session.add(corpus)
        await session.flush()
        version = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="BAAI/bge-m3",
            chunker="header",
            chunker_version="1",
            status="active",
        )
        session.add(version)
        await session.flush()
        corpus.active_index_version_id = version.id
        chunk_ids: list[str] = []
        for i in range(num_chunks):
            blob_key = uuid.uuid4().hex
            doc = Document(
                workspace_id=workspace_id,
                corpus_id=corpus.id,
                blob_uri=blob_key,
                filename=f"{name}-doc{i}.md",
                mime="text/markdown",
                sha256=blob_key,
                source_type="upload",
                status="indexed",
            )
            session.add(doc)
            await session.flush()
            chunk = Chunk(
                workspace_id=workspace_id,
                index_version_id=version.id,
                document_id=doc.id,
                ordinal=i,
                text=f"{name} chunk {i}",
                meta={"document_filename": f"{name}-doc{i}.md", "chunker": "header"},
            )
            session.add(chunk)
            await session.flush()
            chunk_ids.append(chunk.id)
        await session.commit()
        return corpus.id, version.id, chunk_ids


# ---------------------------------------------------------------------------
# Перехват температуры и подмена поиска
# ---------------------------------------------------------------------------


def _complete_body(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _capture_temperature(monkeypatch: pytest.MonkeyPatch, *, response: str = "ok") -> list[float]:
    """Подменяет complete/stream/complete_tools, записывая температуру вызова.

    Возвращает общий список: за один запрос тест делает один вызов модели,
    поэтому единственное значение в списке — то, что ушло провайдеру.
    """
    captured: list[float] = []

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        *,
        temperature: float,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        captured.append(temperature)
        return _complete_body(response)

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        *,
        temperature: float,
        max_tokens: int | None = None,
    ) -> Any:
        captured.append(temperature)
        for word in response.split():
            yield {"type": "token", "v": word + " "}

    async def _stub_complete_tools(
        self: ProviderClient,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        captured.append(temperature)
        return _complete_body(response)

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)
    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)
    monkeypatch.setattr(ProviderClient, "complete_tools", _stub_complete_tools)
    return captured


def _patch_search(monkeypatch: pytest.MonkeyPatch, chunk_ids: list[str]) -> None:
    """Гибридный поиск и реранкинг возвращают заданные чанки — без эмбеддингов."""
    from app.rag.hybrid_search import HybridResult, HybridSearchOutput
    from app.rag.vector_store import Hit

    async def _stub_hybrid_search(
        vector_store: Any,
        embedding_backend: Any,
        index_version_id: str,
        query: str,
        k: int = 50,
    ) -> HybridSearchOutput:
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

    async def _stub_rerank(*args: Any, **kwargs: Any) -> Any:
        from app.rag.reranker import RerankOutput

        results = [
            RerankResult(chunk_id=cid, score=1.0 / (i + 1), text=f"text-{cid}", original_rank=i + 1)
            for i, cid in enumerate(chunk_ids)
        ]
        return RerankOutput(results=results, degraded=False, duration_ms=1.0, error=None)

    monkeypatch.setattr("app.rag.pipeline.hybrid_search", _stub_hybrid_search)
    monkeypatch.setattr("app.rag.pipeline.rerank", _stub_rerank)


def _entry(entries: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return next(item for item in entries if item["key"] == key)


async def _patch_temperature(api_client: httpx.AsyncClient, value: object) -> httpx.Response:
    return await api_client.patch(
        SETTINGS_PATH, json={"key": DEFAULT_TEMPERATURE_KEY, "value": value}
    )


# ---------------------------------------------------------------------------
# Каталог: ключ виден с границами и типом
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_listed_in_generation_category(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    entry = _entry(entries, DEFAULT_TEMPERATURE_KEY)

    assert entry["category"] == GENERATION_CATEGORY
    assert entry["type"] == "number"
    assert entry["min"] == 0.0
    assert entry["max"] == TEMPERATURE_MAX
    assert (entry["value"], entry["source"]) == (ENV_TEMPERATURE, "default")
    assert entry["editable"] is True


# ---------------------------------------------------------------------------
# Путь 1: обычный чат
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_without_temperature_uses_workspace_default(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Поле температуры не прислано — провайдеру уходит настройка области."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)
    captured = _capture_temperature(monkeypatch, response="привет")

    response = await api_client.post(
        CHAT_PATH,
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )

    assert response.status_code == 200, response.text[:300]
    assert captured == [ENV_TEMPERATURE]


@pytest.mark.asyncio
async def test_chat_explicit_temperature_wins(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Явное значение в запросе побеждает настройку области."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)
    captured = _capture_temperature(monkeypatch, response="привет")

    response = await api_client.post(
        CHAT_PATH,
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 1.1,
            "stream": False,
        },
    )

    assert response.status_code == 200, response.text[:300]
    assert captured == [1.1]


@pytest.mark.asyncio
async def test_stream_chat_uses_workspace_default(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Стриминговый путь берёт то же значение, что и не-стриминговый."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)
    captured = _capture_temperature(monkeypatch, response="привет мир")

    response = await api_client.post(
        CHAT_PATH,
        json={"messages": [{"role": "user", "content": "hi"}], "stream": True},
    )

    assert response.status_code == 200, response.text[:300]
    assert captured == [ENV_TEMPERATURE]


# ---------------------------------------------------------------------------
# Путь 2: генерация по документам (RAG)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_generation_uses_workspace_default(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Шаг генерации конвейера получает разрешённое значение через контекст."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)
    _cid, _iv, chunks = await _seed_corpus_with_chunks(app_fixture, "gen-rag")
    captured = _capture_temperature(monkeypatch, response="ответ по документу")
    _patch_search(monkeypatch, chunks)

    response = await api_client.post(
        CHAT_PATH,
        json={
            "messages": [{"role": "user", "content": "что в документе"}],
            "corpus_names": ["gen-rag"],
            "stream": False,
        },
    )

    assert response.status_code == 200, response.text[:300]
    assert response.json()["content"] == "ответ по документу"
    assert captured == [ENV_TEMPERATURE]


# ---------------------------------------------------------------------------
# Путь 3: агентный прогон
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_run_uses_workspace_default(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вызов модели в агентном цикле идёт с настройкой области (своего поля нет)."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture, alias="local/gen-agent", supports_tools=True)
    captured = _capture_temperature(monkeypatch, response="агентный ответ")

    response = await api_client.post(
        AGENT_PATH,
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "model_alias": "local/gen-agent",
        },
    )

    assert response.status_code == 200, response.text[:300]
    assert captured == [ENV_TEMPERATURE]


# ---------------------------------------------------------------------------
# Смена настройки без перезапуска
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_changes_temperature_without_restart(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH значения меняет параметр следующего запроса в том же процессе."""
    await _login(api_client, app_fixture)
    await _seed_model(app_fixture)
    captured = _capture_temperature(monkeypatch, response="привет")

    patched = await _patch_temperature(api_client, 1.5)
    assert patched.status_code == 200, patched.text[:300]
    body = patched.json()
    assert (body["value"], body["source"]) == (1.5, "db")

    response = await api_client.post(
        CHAT_PATH,
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert response.status_code == 200, response.text[:300]
    assert captured == [1.5]


# ---------------------------------------------------------------------------
# Отказы: границы и право
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_out_of_range_values_rejected_and_unchanged(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Значение вне 0–2 не сохраняется; действующее остаётся прежним."""
    await _login(api_client, app_fixture)

    for bad in (TEMPERATURE_MAX + 0.5, -0.1):
        rejected = await _patch_temperature(api_client, bad)
        assert rejected.status_code == 422, rejected.text[:300]

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    entry = _entry(entries, DEFAULT_TEMPERATURE_KEY)
    assert (entry["value"], entry["source"]) == (ENV_TEMPERATURE, "default")


@pytest.mark.asyncio
async def test_role_without_right_cannot_write(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Роль без manage_settings: запись — 404, в каталоге editable=false."""
    await _login(api_client, app_fixture, role_name="developer")

    patched = await _patch_temperature(api_client, 1.5)
    assert patched.status_code == 404, patched.text[:300]

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    entry = _entry(entries, DEFAULT_TEMPERATURE_KEY)
    assert entry["editable"] is False
