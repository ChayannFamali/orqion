"""Т-502: агентный модуль — API, демо-сценарий приёмки.

Пункты пересмотренного дизайн-ревью, проверяемые здесь:

- пункт 2 — синхронный прогон в одном запросе, разговор в режиме ``agent``;
- пункт 6 — модель сама вызывает инструмент поиска по корпусу;
- пункт 7 — ``policy.corpora`` с тем же отказом и перечнем, что обычный
  чат (запрос уровня политики — 403, как в Т-221); гарантию на уровне
  инструмента покрывают юнит-тесты ``test_agent_tools.py``;
- пункт 8 — каждый вызов модели пишет ``usage_event`` (биллинг чата);
- пункт 10 — отдельная точка входа: обычный чат не затронут, диалог
  создаётся в режиме ``agent``;
- деградация — без ``orqion[agent]`` эндпоинт честно сообщает
  о недоступности (паттерн Т-444/Т-505).

Провайдер подменяется заглушкой — обращения к сети запрещены.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import (
    AuditLog,
    Chunk,
    Conversation,
    Corpus,
    Document,
    IndexVersion,
    Message,
    Model,
    Provider,
    Role,
    UsageEvent,
    User,
)
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from app.rag.embeddings import EmbeddedChunk
from app.rag.vector_store import EMBEDDING_DIM
from fastapi import FastAPI
from sqlalchemy import select


def _unit_vec(index: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[index % EMBEDDING_DIM] = 1.0
    return vec


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
            name=f"agent-{role_name}",
            is_builtin=True,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email=f"agent-{role_name}@orqion.local",
            password_hash=hash_password("agent-pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_agent_model(
    app_fixture: FastAPI,
    *,
    supports_tools: bool = True,
    alias: str = "local/agent-model",
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
            alias=alias,
            upstream_name="agent-upstream",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
            supports_tools=supports_tools,
        )
        session.add(model)
        await session.commit()
        return model.id


async def _seed_corpus_with_fragments(
    app_fixture: FastAPI,
    *,
    name: str = "agent-corpus",
    texts: list[str] | None = None,
) -> str:
    """Корпус с активной версией, документом, чанками и векторами."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    vector_store = app_fixture.state.vector_store
    texts = texts or ["Правило отпуска по рецепту", "Срок хранения документов"]
    async with factory() as session:
        corpus = Corpus(name=name, workspace_id=workspace_id, data_class="К0")
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

        doc = Document(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            filename="rules.md",
            mime="text/markdown",
            blob_uri="0" * 64,
            sha256="0" * 64,
            size_bytes=10,
            status="ready",
        )
        session.add(doc)
        await session.flush()

        embedded: list[EmbeddedChunk] = []
        for i, text in enumerate(texts):
            chunk = Chunk(
                workspace_id=workspace_id,
                index_version_id=version.id,
                document_id=doc.id,
                ordinal=i,
                text=text,
                meta={"heading_path": [], "chunker": "header"},
            )
            session.add(chunk)
            await session.flush()
            embedded.append(
                EmbeddedChunk(
                    text=chunk.text,
                    vector=_unit_vec(0),
                    ordinal=i,
                    model="test-embed",
                    chunk_id=chunk.id,
                )
            )
        await session.commit()

    await vector_store.upsert(version.id, embedded)
    return corpus.id


def _tool_call_response(query: str = "правило отпуска") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "search_corpus",
                                "arguments": f'{{"query": "{query}"}}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 2},
    }


def _final_response(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
    }


def _patch_complete_tools(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any]],
) -> dict[str, int]:
    calls = {"n": 0}

    async def _stub(
        self: ProviderClient,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        idx = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[idx]

    monkeypatch.setattr(ProviderClient, "complete_tools", _stub)
    return calls


@pytest.mark.asyncio
async def test_agent_requires_auth(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    resp = await api_client.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "model_alias": "local/agent-model",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_agent_degraded_without_langgraph(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без дополнения orqion[agent] — 200 с available=false и причиной."""
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)

    monkeypatch.setitem(sys.modules, "langgraph", None)
    resp = await api_client.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "model_alias": "local/agent-model",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "orqion[agent]" in body["reason"]


@pytest.mark.asyncio
async def test_agent_rejects_model_without_tools_flag(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Модель без флага supports_tools не запускает агентный цикл."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture, supports_tools=False)
    _patch_complete_tools(monkeypatch, [_final_response("не должно вызываться")])

    resp = await api_client.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "model_alias": "local/agent-model",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"


@pytest.mark.asyncio
async def test_agent_corpus_policy_denial_same_as_chat(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 7: недоступный корпус — тот же отказ с перечнем, что в чате."""
    pytest.importorskip("langgraph")
    # Роль без доступа к корпусам вовсе.
    policy = BUILTIN_ROLES["admin"].model_dump()
    policy["corpora"] = ["allowed-*"]
    await _login(api_client, app_fixture, role_name="restricted", policy=policy)
    await _seed_agent_model(app_fixture)
    await _seed_corpus_with_fragments(app_fixture, name="secret-corpus")
    _patch_complete_tools(monkeypatch, [_final_response("не должно вызываться")])

    resp = await api_client.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "вопрос"}],
            "model_alias": "local/agent-model",
            "corpus_names": ["secret-corpus"],
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "forbidden"
    assert "secret-corpus" in body["constraint"]["corpora"]


@pytest.mark.asyncio
async def test_agent_demo_scenario_full_cycle(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Демо-сценарий: модель вызывает поиск, находит фрагмент, отвечает.

    Каждый шаг — в трассировке и журнале аудита; биллинг шагов — как у
    чата (usage_event на каждый вызов модели); диалог в режиме "agent".
    """
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    await _seed_corpus_with_fragments(app_fixture)

    # Вектор запроса совпадает с направлением чанков → поиск находит их.
    app_fixture.state.embedding_backend.embed.return_value = [_unit_vec(0)]

    calls = _patch_complete_tools(
        monkeypatch,
        [_tool_call_response(), _final_response("Ответ: отпуск по рецепту.")],
    )

    resp = await api_client.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "Как оформляется отпуск?"}],
            "model_alias": "local/agent-model",
            "corpus_names": ["agent-corpus"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["type"] == "complete"
    assert body["content"] == "Ответ: отпуск по рецепту."
    assert calls["n"] == 2  # модель → инструмент → модель

    # Шаги: модель (запрос инструмента), инструмент, модель (ответ).
    kinds = [(s["kind"], s.get("decision")) for s in body["steps"]]
    assert ("model", None) in kinds
    assert ("tool", "allow") in kinds

    # Источники из найденных фрагментов присутствуют.
    assert len(body["sources"]) >= 1

    # Диалог создан в режиме "agent".
    conv_id = body["conversation_id"]
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        conv = (
            await session.execute(select(Conversation).where(Conversation.id == conv_id))
        ).scalar_one()
        assert conv.mode == "agent"

        # Пункт 8: биллинг — отдельный usage_event на каждый вызов модели.
        events = (await session.execute(select(UsageEvent))).scalars().all()
        assert len(events) == 2
        assert all(e.status == "ok" for e in events)

        # Пункт 7: факт вызова инструмента в журнале аудита.
        audits = (await session.execute(select(AuditLog))).scalars().all()
        tool_audits = [a for a in audits if a.action == "agent.tool.search_corpus"]
        assert len(tool_audits) == 1
        assert tool_audits[0].meta is not None
        assert tool_audits[0].meta["decision"] == "allow"


@pytest.mark.asyncio
async def test_agent_confirmation_reject_cancels_without_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 9: отмена подтверждения не вызывает модель и пишет аудит.

    Запрос с ``confirmation_decision="reject"`` возвращает факт отмены;
    провайдер не вызывается вовсе; решение пользователя фиксируется в
    журнале аудита.
    """
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)

    calls = _patch_complete_tools(monkeypatch, [_final_response("не должно вызываться")])

    resp = await api_client.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "Удали кэш"}],
            "model_alias": "local/agent-model",
            "confirmation_decision": "reject",
            "confirmation": {
                "call_id": "call-danger",
                "tool": "wiki.purge_cache",
                "args": {"item": "x"},
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "complete"
    assert body["content"] == "Действие отменено. Инструмент не выполнялся."
    assert body["pending_confirmation"] is None
    assert calls["n"] == 0  # модель не вызывалась

    kinds = [(s["kind"], s.get("decision")) for s in body["steps"]]
    assert ("confirmation", "reject") in kinds

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        audits = (await session.execute(select(AuditLog))).scalars().all()
        confirmations = [a for a in audits if a.action == "agent.tool.confirmation"]
        assert len(confirmations) == 1
        assert confirmations[0].meta is not None
        assert confirmations[0].meta["decision"] == "reject"
        assert confirmations[0].meta["tool"] == "wiki.purge_cache"


@pytest.mark.asyncio
async def test_agent_continue_does_not_duplicate_messages(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Продолжение диалога полным буфером не дублирует сообщения.

    Клиент шлёт буфер целиком; сохраняются только новые сообщения
    (хвост после уже записанных) — иначе каждый ход задваивал бы
    историю.
    """
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)

    _patch_complete_tools(
        monkeypatch,
        [_final_response("Первый ответ"), _final_response("Второй ответ")],
    )

    first = await api_client.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "Первый вопрос"}],
            "model_alias": "local/agent-model",
        },
    )
    assert first.status_code == 200, first.text
    conv_id = first.json()["conversation_id"]

    second = await api_client.post(
        "/api/agent/chat",
        json={
            "messages": [
                {"role": "user", "content": "Первый вопрос"},
                {"role": "assistant", "content": "Первый ответ"},
                {"role": "user", "content": "Второй вопрос"},
            ],
            "model_alias": "local/agent-model",
            "conversation_id": conv_id,
        },
    )
    assert second.status_code == 200, second.text

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        rows = (
            (await session.execute(select(Message.role).where(Message.conversation_id == conv_id)))
            .scalars()
            .all()
        )
        assert list(rows) == ["user", "assistant", "user", "assistant"]
