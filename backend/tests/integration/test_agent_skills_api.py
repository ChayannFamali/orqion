"""Т-508: агентный прогон со скиллом — пункты 2–8 приёмки.

Проверяются решения 2, 5 и 6 дизайн-ревью на реальном эндпоинте:

- пункт 2: модели уходит базовый системный промпт + фрагмент скилла одним
  сообщением (базовый первым), схемы инструментов — пересечение реестра и
  списка скилла, факт в спане ``agent.skill.apply``;
- пункт 3: инструмент вне реестра в прогон не попадает, прогон не падает,
  ответ честно сообщает о недоступном;
- пункт 4: пустой список инструментов скилла = НОЛЬ инструментов;
- пункт 5: несуществующий/отключённый ``skill_id`` — 400, прогон не
  выполняется вовсе;
- пункт 6: К2/К3 — внешние инструменты скилла не в прогоне, факт в
  существующем ``mcp.tools.blocked``, транспорт не тронут;
- пункт 7: деструктивный инструмент через скилл — тот же цикл
  подтверждения (остановка, одобрение исполняет напрямую, отмена без
  вызова модели);
- пункт 8: дефолт ``max_tokens`` скилла проходит ``enforce_all``, биллинг
  каждого вызова модели и лимиты прогона скиллом не ослабляются.

Провайдер и транспорт протокола подменяются заглушками — обращения к сети
нет. Тесты с внешними инструментами требуют ``orqion[mcp]`` и
пропускаются без него.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx
import pytest
from app.agent.loop import AGENT_SYSTEM_PROMPT
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import (
    AgentSkill,
    AuditLog,
    Chunk,
    Conversation,
    Corpus,
    Document,
    IndexVersion,
    McpServer,
    Message,
    Model,
    Provider,
    Role,
    Span,
    Trace,
    UsageEvent,
    User,
)
from app.mcp.client import DiscoveredTool, ToolCallResult
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
    *,
    policy: dict[str, Any] | None = None,
    tag: str = "skill",
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role_policy = policy or BUILTIN_ROLES["admin"].model_dump()
        role = Role(
            workspace_id=workspace_id,
            name=f"{tag}-role",
            is_builtin=True,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email=f"{tag}-user@orqion.local",
            password_hash=hash_password("pass-123"),
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
    alias: str = "local/agent-model",
    *,
    max_output_tokens: int | None = None,
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
            max_output_tokens=max_output_tokens,
            enabled=True,
            supports_tools=True,
        )
        session.add(model)
        await session.commit()
        return model.id


async def _seed_skill(
    app_fixture: FastAPI,
    *,
    name: str = "Разбор",
    prompt_text: str = "",
    tools: list[str] | None = None,
    default_max_tokens: int | None = None,
    enabled: bool = True,
) -> str:
    """Скилл пишется в БД напрямую: CRUD покрыт test_skills_api.py."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        skill = AgentSkill(
            workspace_id=workspace_id,
            name=name,
            description="",
            prompt_text=prompt_text,
            tools=tools if tools is not None else [],
            default_max_tokens=default_max_tokens,
            enabled=enabled,
        )
        session.add(skill)
        await session.commit()
        return skill.id


async def _seed_mcp_server(app_fixture: FastAPI, *, name: str = "demo") -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        server = McpServer(
            workspace_id=workspace_id,
            name=name,
            url="http://stub-mcp:9210/mcp",
            api_key_enc=None,
            enabled=True,
        )
        session.add(server)
        await session.commit()
        return server.id


def _patch_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tools: list[DiscoveredTool],
) -> dict[str, int]:
    """Подменяет обнаружение инструментов: транспорт не вызывается."""
    stats = {"calls": 0}

    async def _fake_discover(conn: Any, timeout: float) -> list[DiscoveredTool]:
        stats["calls"] += 1
        return list(tools)

    monkeypatch.setattr("app.mcp.client.discover_tools", _fake_discover)
    return stats


def _patch_call_tool(monkeypatch: pytest.MonkeyPatch, text: str = "готово") -> list[dict[str, Any]]:
    """Подменяет вызов инструмента сервера: транспорт не вызывается."""
    calls: list[dict[str, Any]] = []

    async def _fake_call(
        conn: Any, tool_name: str, arguments: dict[str, object], timeout: float
    ) -> ToolCallResult:
        calls.append({"tool_name": tool_name, "arguments": arguments})
        return ToolCallResult(text=text, is_error=False)

    monkeypatch.setattr("app.mcp.client.call_tool", _fake_call)
    return calls


async def _seed_corpus(
    app_fixture: FastAPI,
    *,
    name: str = "skill-corpus",
    data_class: str = "К0",
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    vector_store = app_fixture.state.vector_store
    async with factory() as session:
        corpus = Corpus(name=name, workspace_id=workspace_id, data_class=data_class)
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

        chunk = Chunk(
            workspace_id=workspace_id,
            index_version_id=version.id,
            document_id=doc.id,
            ordinal=0,
            text="Правило отпуска по рецепту",
            meta={"heading_path": [], "chunker": "header"},
        )
        session.add(chunk)
        await session.flush()
        embedded = [
            EmbeddedChunk(
                text=chunk.text,
                vector=_unit_vec(0),
                ordinal=0,
                model="test-embed",
                chunk_id=chunk.id,
            )
        ]
        await session.commit()

    await vector_store.upsert(version.id, embedded)
    return corpus.id


def _tool_call_response(name: str, arguments: str = '{"query": "отпуск"}') -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
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


def _capture_model_calls(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Перехватывает вызовы провайдера, сохраняя сообщения и схемы инструментов."""
    calls: list[dict[str, Any]] = []

    async def _stub(
        self: ProviderClient,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        idx = min(len(calls), len(responses) - 1)
        calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        return responses[idx]

    monkeypatch.setattr(ProviderClient, "complete_tools", _stub)
    return calls


def _tool_names(calls: list[dict[str, Any]], index: int = 0) -> list[str]:
    return [t["function"]["name"] for t in calls[index]["tools"]]


def _system_contents(calls: list[dict[str, Any]], index: int = 0) -> list[str]:
    return [m["content"] for m in calls[index]["messages"] if m["role"] == "system"]


async def _spans(app_fixture: FastAPI, name: str) -> list[Span]:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        traces = (await session.execute(select(Trace))).scalars().all()
        if not traces:
            return []
        rows = (
            (
                await session.execute(
                    select(Span).where(
                        Span.trace_id == traces[-1].id,
                        Span.name == name,
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)


async def _audit_actions(app_fixture: FastAPI) -> list[str]:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        rows = (await session.execute(select(AuditLog.action))).scalars().all()
        return list(rows)


def _demo_tools() -> list[DiscoveredTool]:
    """Два инструмента сервера: обычный и деструктивный (аннотация протокола)."""
    return [
        DiscoveredTool(
            server_tool_name="get_build_status",
            description="Статус сборки",
            input_schema={"type": "object", "properties": {}},
            destructive=False,
        ),
        DiscoveredTool(
            server_tool_name="delete_build_artifacts",
            description="Удаляет артефакты сборки",
            input_schema={"type": "object", "properties": {}},
            destructive=True,
        ),
    ]


async def _post_chat(
    api_client: httpx.AsyncClient,
    *,
    skill_id: str | None,
    content: str = "Как оформляется отпуск?",
    corpus_names: list[str] | None = None,
    max_tokens: int | None = None,
    confirmation_decision: str | None = None,
    confirmation: dict[str, Any] | None = None,
) -> httpx.Response:
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": content}],
        "model_alias": "local/agent-model",
        "skill_id": skill_id,
    }
    if corpus_names is not None:
        payload["corpus_names"] = corpus_names
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if confirmation_decision is not None:
        payload["confirmation_decision"] = confirmation_decision
    if confirmation is not None:
        payload["confirmation"] = confirmation
    return await api_client.post("/api/agent/chat", json=payload)


@pytest.mark.asyncio
async def test_skill_appends_prompt_and_narrows_tools(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 2: фрагмент дописан к базовому промпту, инструменты сужены."""
    pytest.importorskip("langgraph")
    pytest.importorskip("mcp")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    await _seed_corpus(app_fixture)
    await _seed_mcp_server(app_fixture)
    _patch_discovery(monkeypatch, _demo_tools())
    skill_id = await _seed_skill(
        app_fixture,
        prompt_text="Отвечай строго по найденным документам.",
        tools=["search_corpus", "demo.get_build_status"],
    )

    app_fixture.state.embedding_backend.embed.return_value = [_unit_vec(0)]
    calls = _capture_model_calls(
        monkeypatch,
        [_tool_call_response("search_corpus"), _final_response("Ответ по документам.")],
    )

    resp = await _post_chat(api_client, skill_id=skill_id, corpus_names=["skill-corpus"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"] == "Ответ по документам."

    # Системное сообщение одно: базовый промпт первым, фрагмент дописан.
    systems = _system_contents(calls)
    assert len(systems) == 1
    assert systems[0].startswith(AGENT_SYSTEM_PROMPT)
    assert systems[0].endswith("Отвечай строго по найденным документам.")

    # Деструктивный инструмент сервера в скилл не входит → модели не ушёл.
    assert _tool_names(calls) == ["search_corpus", "demo.get_build_status"]
    assert "demo.delete_build_artifacts" not in _tool_names(calls)

    # Шаг скилла — первым в ленте, до вызовов модели.
    assert body["steps"][0]["kind"] == "skill"
    assert body["steps"][0]["name"] == "Разбор"
    assert "2" in body["steps"][0]["summary"]
    assert body["skill_tools_unavailable"] == []

    # Факт сужения — в спане трассировки.
    spans = await _spans(app_fixture, "agent.skill.apply")
    assert len(spans) == 1
    payload = spans[0].payload
    assert payload is not None
    assert payload["skill_id"] == skill_id
    assert payload["requested_tools"] == ["search_corpus", "demo.get_build_status"]
    assert payload["effective_tools"] == ["search_corpus", "demo.get_build_status"]
    registry_before = payload["registry_tools_before"]
    assert isinstance(registry_before, list)
    assert "demo.delete_build_artifacts" in registry_before
    assert payload["dropped"] == []

    # Пункт 8: биллинг каждого вызова модели скиллом не ослаблен.
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        events = (await session.execute(select(UsageEvent))).scalars().all()
        assert len(events) == 2
        assert all(e.status == "ok" for e in events)


@pytest.mark.asyncio
async def test_skill_without_prompt_keeps_base_only(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пустой фрагмент не меняет системный промпт (никаких лишних разделителей)."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    skill_id = await _seed_skill(app_fixture, prompt_text="   ", tools=["search_corpus"])

    calls = _capture_model_calls(monkeypatch, [_final_response("Ответ")])
    resp = await _post_chat(api_client, skill_id=skill_id)
    assert resp.status_code == 200, resp.text

    assert _system_contents(calls) == [AGENT_SYSTEM_PROMPT]


@pytest.mark.asyncio
async def test_skill_empty_tools_sends_no_tools(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 4: пустой список = НОЛЬ инструментов, включая встроенный поиск."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    await _seed_corpus(app_fixture)
    skill_id = await _seed_skill(app_fixture, prompt_text="Только инструкции", tools=[])

    app_fixture.state.embedding_backend.embed.return_value = [_unit_vec(0)]
    calls = _capture_model_calls(monkeypatch, [_final_response("Ответ без инструментов")])

    resp = await _post_chat(api_client, skill_id=skill_id, corpus_names=["skill-corpus"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert calls[0]["tools"] == []
    assert body["steps"][0]["kind"] == "skill"
    assert "0" in body["steps"][0]["summary"]

    spans = await _spans(app_fixture, "agent.skill.apply")
    assert spans[0].payload is not None
    assert spans[0].payload["effective_tools"] == []


@pytest.mark.asyncio
async def test_skill_tool_outside_registry_dropped_and_reported(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 3: инструмент вне реестра не попадает в прогон, прогон не падает."""
    pytest.importorskip("langgraph")
    pytest.importorskip("mcp")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    # Сервера в реестре нет вовсе → «wiki.lookup» не существует.
    skill_id = await _seed_skill(app_fixture, tools=["search_corpus", "wiki.lookup", "ghost.tool"])

    calls = _capture_model_calls(monkeypatch, [_final_response("Ответ")])
    resp = await _post_chat(api_client, skill_id=skill_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert _tool_names(calls) == ["search_corpus"]
    assert body["skill_tools_unavailable"] == ["wiki.lookup", "ghost.tool"]

    spans = await _spans(app_fixture, "agent.skill.apply")
    payload = spans[0].payload
    assert payload is not None
    dropped = payload["dropped"]
    assert isinstance(dropped, list)
    assert {d["name"]: d["reason"] for d in dropped} == {
        "wiki.lookup": "not_in_registry",
        "ghost.tool": "not_in_registry",
    }


@pytest.mark.asyncio
async def test_skill_disabled_server_tool_dropped(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Выключенный сервер: его инструмент не в реестре → скилл остаётся без него."""
    pytest.importorskip("langgraph")
    pytest.importorskip("mcp")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        session.add(
            McpServer(
                workspace_id=workspace_id,
                name="demo",
                url="http://stub-mcp:9210/mcp",
                api_key_enc=None,
                enabled=False,
            )
        )
        await session.commit()

    discovery = _patch_discovery(monkeypatch, _demo_tools())
    skill_id = await _seed_skill(app_fixture, tools=["demo.get_build_status"])

    calls = _capture_model_calls(monkeypatch, [_final_response("Ответ")])
    resp = await _post_chat(api_client, skill_id=skill_id)
    assert resp.status_code == 200, resp.text

    assert calls[0]["tools"] == []
    assert resp.json()["skill_tools_unavailable"] == ["demo.get_build_status"]
    # Выключенный сервер не обнаруживается вовсе.
    assert discovery["calls"] == 0


@pytest.mark.asyncio
async def test_skill_missing_id_rejected_400(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 5: несуществующий skill_id — 400 с явной причиной, прогона нет."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)

    calls = _capture_model_calls(monkeypatch, [_final_response("не должно вызываться")])
    resp = await _post_chat(api_client, skill_id="no-such-skill")

    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"] == "skill_not_available"
    assert body["reason"] == "Скилл не найден или отключён"
    assert body["constraint"] == {"skill_id": "no-such-skill"}
    assert calls == []  # модель не вызывалась

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        # Прогон не выполнялся: ни диалога, ни биллинга.
        assert (await session.execute(select(Conversation))).scalars().all() == []
        assert (await session.execute(select(UsageEvent))).scalars().all() == []


@pytest.mark.asyncio
async def test_skill_disabled_id_rejected_400(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 5: отключённый скилл недоступен для прогона — тот же явный отказ."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    skill_id = await _seed_skill(app_fixture, tools=["search_corpus"], enabled=False)

    calls = _capture_model_calls(monkeypatch, [_final_response("не должно вызываться")])
    resp = await _post_chat(api_client, skill_id=skill_id)

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "skill_not_available"
    assert calls == []


@pytest.mark.asyncio
async def test_skill_k2_blocks_external_before_transport(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 6: К2 — внешние инструменты скилла не в прогоне, транспорт не тронут."""
    pytest.importorskip("langgraph")
    pytest.importorskip("mcp")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    await _seed_corpus(app_fixture, name="k2-corpus", data_class="К2")
    await _seed_mcp_server(app_fixture)
    skill_id = await _seed_skill(app_fixture, tools=["search_corpus", "demo.get_build_status"])

    # Обнаружение не должно вызываться вовсе: отказ случается до него.
    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("обнаружение не должно вызываться при К2/К3")

    monkeypatch.setattr("app.mcp.client.discover_tools", _explode)

    app_fixture.state.embedding_backend.embed.return_value = [_unit_vec(0)]
    calls = _capture_model_calls(monkeypatch, [_final_response("Ответ по К2")])

    resp = await _post_chat(api_client, skill_id=skill_id, corpus_names=["k2-corpus"])
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Модели ушёл только встроенный поиск.
    assert _tool_names(calls) == ["search_corpus"]
    assert body["skill_tools_unavailable"] == ["demo.get_build_status"]

    spans = await _spans(app_fixture, "agent.skill.apply")
    payload = spans[0].payload
    assert payload is not None
    assert payload["blocked_external"] == "К2"
    assert payload["dropped"] == [{"name": "demo.get_build_status", "reason": "blocked_external"}]

    # Отказ зафиксирован существующей записью Т-503; новой — нет.
    actions = await _audit_actions(app_fixture)
    assert "mcp.tools.blocked" in actions
    assert "mcp.server.unavailable" not in actions
    # Скиллы сеются в БД напрямую, поэтому записей изменения скилла нет:
    # сужение само по себе аудит не создаёт (решение 2, ADR-21 п. 2).
    assert "agent_skill.changed" not in actions


@pytest.mark.asyncio
async def test_skill_destructive_tool_stops_for_confirmation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 7: деструктивный инструмент через скилл — остановка до выполнения."""
    pytest.importorskip("langgraph")
    pytest.importorskip("mcp")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    await _seed_mcp_server(app_fixture)
    _patch_discovery(monkeypatch, _demo_tools())
    tool_calls = _patch_call_tool(monkeypatch)
    skill_id = await _seed_skill(
        app_fixture,
        prompt_text="Удаляй артефакты по запросу.",
        tools=["demo.delete_build_artifacts"],
    )

    calls = _capture_model_calls(
        monkeypatch,
        [
            _tool_call_response("demo.delete_build_artifacts", '{"project": "orqion"}'),
            _final_response("не должно вызываться"),
        ],
    )

    resp = await _post_chat(api_client, skill_id=skill_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["pending_confirmation"] is not None
    assert body["pending_confirmation"]["tool"] == "demo.delete_build_artifacts"
    assert body["pending_confirmation"]["args"] == {"project": "orqion"}
    assert tool_calls == []  # инструмент не выполнялся
    assert len(calls) == 1  # второго вызова модели не было

    kinds = [(s["kind"], s.get("decision")) for s in body["steps"]]
    assert ("skill", None) in kinds
    assert ("confirmation", "pending") in kinds


@pytest.mark.asyncio
async def test_skill_destructive_approve_executes_directly(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 7: одобрение исполняет вызов напрямую, сужение скиллом сохраняется."""
    pytest.importorskip("langgraph")
    pytest.importorskip("mcp")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    await _seed_mcp_server(app_fixture)
    _patch_discovery(monkeypatch, _demo_tools())
    tool_calls = _patch_call_tool(monkeypatch, text="артефакты удалены")
    skill_id = await _seed_skill(app_fixture, tools=["demo.delete_build_artifacts"])

    # Перехват ставится до первого запроса: иначе первый прогон ушёл бы на
    # настоящий адрес провайдера-заглушки.
    calls = _capture_model_calls(
        monkeypatch,
        [
            _tool_call_response("demo.delete_build_artifacts", '{"project": "orqion"}'),
            _final_response("Готово, удалил."),
        ],
    )

    first = await _post_chat(api_client, skill_id=skill_id, content="Удали артефакты")
    assert first.status_code == 200, first.text
    pending = first.json()["pending_confirmation"]
    assert pending is not None
    conv_id = first.json()["conversation_id"]
    assert len(calls) == 1  # прогон остановлен до выполнения
    assert tool_calls == []

    second = await api_client.post(
        "/api/agent/chat",
        json={
            "messages": [
                {"role": "user", "content": "Удали артефакты"},
                {"role": "assistant", "content": first.json()["content"]},
            ],
            "model_alias": "local/agent-model",
            "conversation_id": conv_id,
            "skill_id": skill_id,
            "confirmation_decision": "approve",
            "confirmation": pending,
        },
    )
    assert second.status_code == 200, second.text
    body = second.json()

    # Подтверждённый вызов исполнен напрямую: транспорт вызван один раз.
    assert tool_calls == [
        {"tool_name": "delete_build_artifacts", "arguments": {"project": "orqion"}}
    ]
    # На запросе одобрения модель вызвана один раз — для итогового ответа,
    # а не для повторного запроса инструмента (механика fa93381).
    assert len(calls) == 2
    # Сужение скиллом сохраняется и на пути одобрения: модели снова ушёл
    # только инструмент из скилла, а не полный реестр.
    assert _tool_names(calls, 1) == ["demo.delete_build_artifacts"]

    kinds = [(s["kind"], s.get("decision")) for s in body["steps"]]
    assert ("confirmation", "approve") in kinds

    actions = await _audit_actions(app_fixture)
    assert "agent.tool.mcp" in actions
    assert "agent.tool.confirmation" in actions


@pytest.mark.asyncio
async def test_skill_destructive_reject_without_model_call(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 7: отмена через скилл — без вызова модели, шаг скилла в ленте."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture)
    await _seed_agent_model(app_fixture)
    skill_id = await _seed_skill(app_fixture, tools=["search_corpus"])

    calls = _capture_model_calls(monkeypatch, [_final_response("не должно вызываться")])
    resp = await _post_chat(
        api_client,
        skill_id=skill_id,
        confirmation_decision="reject",
        confirmation={
            "call_id": "call-1",
            "tool": "demo.delete_build_artifacts",
            "args": {"project": "orqion"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert calls == []
    assert body["content"] == "Действие отменено. Инструмент не выполнялся."
    kinds = [(s["kind"], s.get("decision")) for s in body["steps"]]
    assert ("skill", None) in kinds
    assert ("confirmation", "reject") in kinds
    # Список недоступных инструментов скилла считается по его собственному
    # списку, а не по инструменту из запроса подтверждения.
    assert body["skill_tools_unavailable"] == []


@pytest.mark.asyncio
async def test_skill_default_max_tokens_passes_policy(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 8: дефолт скилла — не переопределение потолка, а вход политики."""
    pytest.importorskip("langgraph")
    policy = BUILTIN_ROLES["admin"].model_dump()
    policy["max_output_tokens"] = 100
    await _login(api_client, app_fixture, policy=policy, tag="limited")
    # У модели задан потолок вывода: каждый модельный шаг в цикле (Т-502)
    # проверяется enforce_all именно по нему, поэтому без этого значения
    # прогон не прошёл бы проверку шага независимо от скилла.
    await _seed_agent_model(app_fixture, max_output_tokens=50)
    skill_id = await _seed_skill(app_fixture, tools=["search_corpus"], default_max_tokens=500)

    calls = _capture_model_calls(monkeypatch, [_final_response("не должно вызываться")])

    # Дефолт скилла выше лимита роли → отказ политики, прогона нет.
    denied = await _post_chat(api_client, skill_id=skill_id)
    assert denied.status_code == 413, denied.text
    body = denied.json()
    assert body["error"] == "context_limit_exceeded"
    # Ключевая проверка: в enforce_all вошло именно значение скилла (500),
    # а не запасной 1024 — значит дефолт скилла реально участвует в политике.
    assert body["constraint"] == {"limit": 100, "actual": 500, "type": "output"}
    assert calls == []  # модель не вызывалась

    # Явное значение запроса перебивает дефолт скилла → прогон разрешён.
    allowed = await _post_chat(api_client, skill_id=skill_id, max_tokens=50)
    assert allowed.status_code == 200, allowed.text
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_skill_does_not_weaken_run_token_limit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт 8: лимит прогона действует и со скиллом."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, tag="limits")
    await _seed_agent_model(app_fixture)
    skill_id = await _seed_skill(app_fixture, tools=["search_corpus"])

    # Лимит прогона берётся из настроек приложения, созданных при старте,
    # поэтому подменяется атрибут экземпляра, а не переменная окружения.
    monkeypatch.setattr(app_fixture.state.settings, "agent_max_tokens_per_run", 1)
    _capture_model_calls(
        monkeypatch,
        [_tool_call_response("search_corpus"), _final_response("не должно вызываться")],
    )

    resp = await _post_chat(api_client, skill_id=skill_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "error"
    assert body["code"] == "agent_run_limit_exceeded"
    assert body["constraint"] is not None
    assert body["constraint"]["type"] == "tokens"


@pytest.mark.asyncio
async def test_agent_without_skill_keeps_full_registry(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без скилла поведение Т-503 неизменно: реестр полный, шага скилла нет."""
    pytest.importorskip("langgraph")
    pytest.importorskip("mcp")
    await _login(api_client, app_fixture, tag="noskill")
    await _seed_agent_model(app_fixture)
    await _seed_mcp_server(app_fixture)
    _patch_discovery(monkeypatch, _demo_tools())
    # Скилл существует, но не выбран — на прогон влиять не должен.
    await _seed_skill(app_fixture, name="Не выбран", tools=["search_corpus"])

    calls = _capture_model_calls(monkeypatch, [_final_response("Обычный ответ")])
    resp = await _post_chat(api_client, skill_id=None)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert _tool_names(calls) == [
        "search_corpus",
        "demo.get_build_status",
        "demo.delete_build_artifacts",
    ]
    assert all(s["kind"] != "skill" for s in body["steps"])
    assert body["skill_tools_unavailable"] == []
    assert await _spans(app_fixture, "agent.skill.apply") == []


@pytest.mark.asyncio
async def test_skill_conversation_stays_agent_mode(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 5: скилл не меняет способ хранения диалога — колонки скилла нет."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, tag="convmode")
    await _seed_agent_model(app_fixture)
    skill_id = await _seed_skill(app_fixture, tools=["search_corpus"])

    _capture_model_calls(monkeypatch, [_final_response("Ответ")])
    resp = await _post_chat(api_client, skill_id=skill_id)
    assert resp.status_code == 200, resp.text
    conv_id = resp.json()["conversation_id"]

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        conv = (
            await session.execute(select(Conversation).where(Conversation.id == conv_id))
        ).scalar_one()
        assert conv.mode == "agent"
        assert not hasattr(conv, "skill_id")
        messages = (
            (await session.execute(select(Message.role).where(Message.conversation_id == conv_id)))
            .scalars()
            .all()
        )
        assert list(messages) == ["user", "assistant"]


@pytest.mark.asyncio
async def test_skill_degraded_without_langgraph(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Деградация без orqion[agent] важнее выбора скилла: available=false."""
    await _login(api_client, app_fixture, tag="degraded")
    await _seed_agent_model(app_fixture)
    skill_id = await _seed_skill(app_fixture, tools=["search_corpus"])

    monkeypatch.setitem(sys.modules, "langgraph", None)
    resp = await _post_chat(api_client, skill_id=skill_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "orqion[agent]" in body["reason"]
