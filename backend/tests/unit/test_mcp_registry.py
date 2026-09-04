"""Т-503: единый реестр инструментов прогона и исполнение внешних вызовов.

Проверяются решения 4 и 6 дизайн-ревью и перенос ADR-21 буквально:

- один список реестра с меткой источника, неймспейсинг
  ``<имя_сервера>.<имя_инструмента>``;
- класс данных К2/К3 отклоняет вынос на внешние серверы до обнаружения;
- недоступный сервер скрывает инструменты, факт пишется в журнал аудита
  в самом пути сборки;
- каждый вызов внешнего инструмента — дуальный аудит (span + журнал),
  сбой транспорта не роняет прогон.

Позитивные транспортные тесты требуют установленный ``mcp`` (экстра
``orqion[mcp]``) и пропускаются без него.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.agent.tools import (
    SEARCH_CORPUS_SPEC,
    ResolvedTools,
    ServerEndpoint,
    ToolRunContext,
    ToolSpec,
    execute_mcp_tool,
)
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.models import AuditLog, McpServer, Model, Provider, Role, User, Workspace
from app.mcp.registry import resolve_tools
from app.policy.models import Policy
from app.rag.vector_store import EMBEDDING_DIM, SQLiteVectorStore
from app.trace.service import TraceContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def vector_stores() -> AsyncIterator[list[SQLiteVectorStore]]:
    """Хранилища, созданные тестом, закрываются после него (иначе процесс виснет)."""
    stores: list[SQLiteVectorStore] = []
    yield stores
    for store in stores:
        await store.close()


@pytest.fixture
async def live_mcp_server() -> AsyncIterator[str]:
    """Реальный сервер протокола (FastMCP) на свободном порту.

    Два инструмента: ``echo`` и деструктивный ``drop_cache`` (заявлен
    аннотацией протокола ``destructiveHint``); транспорт — streamable
    HTTP, путь ``/mcp``.
    """
    pytest.importorskip("mcp")
    import uvicorn
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    server = FastMCP("demo-server")

    def echo(text: str) -> str:
        """Возвращает текст как есть."""
        return f"echo: {text}"

    server.tool()(echo)

    def drop_cache(item: str) -> str:
        """Удаляет элемент кэша (деструктивное действие)."""
        return f"deleted: {item}"

    server.tool(annotations=ToolAnnotations(destructiveHint=True))(drop_cache)

    app = server.streamable_http_app()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            conn = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            conn.close()
            break
        except OSError:
            await asyncio.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    uv_server.should_exit = True
    thread.join(timeout=5)


def _free_port() -> int:
    """Порт без слушателя — для имитации недоступного сервера."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port: int = probe.getsockname()[1]
    probe.close()
    return port


async def _seed_workspace(db_session: AsyncSession) -> tuple[str, str]:
    ws = Workspace(name="mcp-registry-test")
    db_session.add(ws)
    await db_session.flush()
    role = Role(
        workspace_id=ws.id,
        name="mcp-registry-role",
        is_builtin=False,
        policy={"models": ["*"], "corpora": ["*"]},
    )
    db_session.add(role)
    await db_session.flush()
    user = User(
        workspace_id=ws.id,
        email="mcp-registry@orqion.local",
        password_hash=hash_password("pass-123"),
        role_id=role.id,
    )
    db_session.add(user)
    await db_session.flush()
    return ws.id, user.id


def _trace(workspace_id: str) -> TraceContext:
    return TraceContext(trace_id="trace-registry", workspace_id=workspace_id)


async def _make_tctx(
    db_session: AsyncSession,
    settings: Settings,
    workspace_id: str,
    user_id: str,
    data_class: str | None,
    vector_stores: list[SQLiteVectorStore],
) -> ToolRunContext:
    """Минимальный контекст инструментов: хранилище и поиск не нужны."""
    embedding_backend = AsyncMock()
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = 1.0
    embedding_backend.embed.return_value = [vector]
    provider = Provider(
        workspace_id=workspace_id,
        kind="openai",
        base_url="http://stub:1234",
        api_key_enc=None,
        enabled=True,
        capabilities={},
    )
    db_session.add(provider)
    await db_session.flush()
    model = Model(
        workspace_id=workspace_id,
        provider_id=provider.id,
        alias="local/mcp-test",
        upstream_name="upstream",
        locality="local",
        enabled=True,
        supports_tools=True,
    )
    db_session.add(model)
    await db_session.flush()
    store = SQLiteVectorStore(":memory:")
    vector_stores.append(store)
    return ToolRunContext(
        session=db_session,
        settings=settings,
        vector_store=store,
        embedding_backend=embedding_backend,
        secret_key="test-secret",
        workspace_id=workspace_id,
        user_id=user_id,
        policy=Policy(models=["*"], corpora=["*"]),
        corpora=[],
        corpus_names=[],
        corpus_data_class=data_class,
        model=model,
        provider=provider,
        trace_ctx=_trace(workspace_id),
        conversation_id=None,
    )


@pytest.mark.asyncio
async def test_resolve_tools_builtin_only_without_servers(
    db_session: AsyncSession,
    test_settings: Settings,
) -> None:
    """Без серверов в реестре только встроенные инструменты."""
    workspace_id, user_id = await _seed_workspace(db_session)

    registry = await resolve_tools(
        db_session,
        test_settings,
        secret_key="test-secret",
        workspace_id=workspace_id,
        user_id=user_id,
        trace_ctx=_trace(workspace_id),
        conversation_id=None,
        corpus_data_class="К0",
    )

    assert [s.name for s in registry.specs] == [SEARCH_CORPUS_SPEC.name]
    assert registry.blocked_external is None
    assert registry.servers == {}


@pytest.mark.asyncio
async def test_resolve_tools_blocks_external_for_k2(
    db_session: AsyncSession,
    test_settings: Settings,
) -> None:
    """К2/К3: внешние инструменты не входят в реестр, факт — в журнал аудита.

    Сервер в реестре есть, но обращение к нему не происходит: записи о
    недоступности нет — отказ случился до обнаружения (пункт 8 ADR-21).
    """
    workspace_id, user_id = await _seed_workspace(db_session)
    db_session.add(
        McpServer(
            workspace_id=workspace_id,
            name="wiki",
            url=f"http://127.0.0.1:{_free_port()}/mcp",
            api_key_enc=None,
            enabled=True,
        )
    )
    await db_session.commit()

    registry = await resolve_tools(
        db_session,
        test_settings,
        secret_key="test-secret",
        workspace_id=workspace_id,
        user_id=user_id,
        trace_ctx=_trace(workspace_id),
        conversation_id=None,
        corpus_data_class="К2",
    )

    assert [s.name for s in registry.specs] == [SEARCH_CORPUS_SPEC.name]
    assert registry.blocked_external == "К2"

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    blocked = [r for r in rows if r.action == "mcp.tools.blocked"]
    assert len(blocked) == 1
    assert blocked[0].meta is not None
    assert blocked[0].meta["decision"] == "deny"
    assert blocked[0].meta["data_class"] == "К2"
    assert not [r for r in rows if r.action == "mcp.server.unavailable"]


@pytest.mark.asyncio
async def test_resolve_tools_unavailable_server_hidden_and_audited(
    db_session: AsyncSession,
    test_settings: Settings,
) -> None:
    """Решение 6: недоступный сервер скрывает инструменты, факт в пути сборки."""
    pytest.importorskip("mcp")
    workspace_id, user_id = await _seed_workspace(db_session)
    db_session.add(
        McpServer(
            workspace_id=workspace_id,
            name="dead",
            url=f"http://127.0.0.1:{_free_port()}/mcp",
            api_key_enc=None,
            enabled=True,
        )
    )
    await db_session.commit()

    registry = await resolve_tools(
        db_session,
        test_settings,
        secret_key="test-secret",
        workspace_id=workspace_id,
        user_id=user_id,
        trace_ctx=_trace(workspace_id),
        conversation_id=None,
        corpus_data_class=None,
    )

    # Встроенные остались; инструменты недоступного сервера скрыты.
    assert [s.name for s in registry.specs] == [SEARCH_CORPUS_SPEC.name]
    assert registry.servers == {}

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    hidden = [r for r in rows if r.action == "mcp.server.unavailable"]
    assert len(hidden) == 1
    assert hidden[0].meta is not None
    assert hidden[0].meta["server_name"] == "dead"
    assert hidden[0].meta["error"]


@pytest.mark.asyncio
async def test_resolve_tools_live_server_namespaced(
    db_session: AsyncSession,
    test_settings: Settings,
    live_mcp_server: str,
) -> None:
    """Решение 4: инструменты сервера входят в единый реестр под неймспейсом."""
    workspace_id, user_id = await _seed_workspace(db_session)
    db_session.add(
        McpServer(
            workspace_id=workspace_id,
            name="demo",
            url=live_mcp_server,
            api_key_enc=None,
            enabled=True,
        )
    )
    await db_session.commit()

    registry = await resolve_tools(
        db_session,
        test_settings,
        secret_key="test-secret",
        workspace_id=workspace_id,
        user_id=user_id,
        trace_ctx=_trace(workspace_id),
        conversation_id=None,
        corpus_data_class=None,
    )

    names = [s.name for s in registry.specs]
    assert SEARCH_CORPUS_SPEC.name in names
    assert "demo.echo" in names
    echo = registry.spec_by_name("demo.echo")
    assert echo is not None
    assert echo.source == "mcp:demo"
    assert echo.server_name == "demo"
    assert echo.mcp_tool_name == "echo"
    assert echo.destructive is False
    assert registry.servers["demo"].url == live_mcp_server

    # Пункт 9: деструктивность приходит из протокола (аннотация
    # ``destructiveHint``), а не из настроек.
    assert "demo.drop_cache" in names
    drop = registry.spec_by_name("demo.drop_cache")
    assert drop is not None
    assert drop.destructive is True

    schemas = registry.schemas()
    assert "demo.echo" in [s["function"]["name"] for s in schemas]
    assert "demo.drop_cache" in [s["function"]["name"] for s in schemas]


@pytest.mark.asyncio
async def test_execute_mcp_tool_denied_before_network_for_k2(
    db_session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Отказ К2/К3 — до любого обращения к транспорту (защита в глубине)."""
    workspace_id, user_id = await _seed_workspace(db_session)
    tctx = await _make_tctx(db_session, test_settings, workspace_id, user_id, "К3", vector_stores)

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("транспорт не должен вызываться при К2/К3")

    monkeypatch.setattr("app.mcp.client.call_tool", _explode)

    spec = ToolSpec(
        name="wiki.lookup",
        description="внешний инструмент",
        parameters={"type": "object", "properties": {}},
        destructive=False,
        source="mcp:wiki",
        server_name="wiki",
        mcp_tool_name="lookup",
    )
    registry = ResolvedTools(
        specs=[spec], servers={"wiki": ServerEndpoint(url="http://x", api_key_enc=None)}
    )

    outcome = await execute_mcp_tool(spec, {"q": "1"}, tctx, registry)

    assert outcome.decision == "deny"
    assert "К2/К3" in outcome.text

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    calls = [r for r in rows if r.action == "agent.tool.mcp"]
    assert len(calls) == 1
    assert calls[0].meta is not None
    assert calls[0].meta["decision"] == "deny"
    assert calls[0].meta["server_name"] == "wiki"


@pytest.mark.asyncio
async def test_execute_mcp_tool_transport_failure_survives(
    db_session: AsyncSession,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Сбой транспорта не роняет прогон: текст ошибки модели, факт в журнал."""
    workspace_id, user_id = await _seed_workspace(db_session)
    tctx = await _make_tctx(db_session, test_settings, workspace_id, user_id, None, vector_stores)

    async def _fail(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError("connection refused")

    monkeypatch.setattr("app.mcp.client.call_tool", _fail)

    spec = ToolSpec(
        name="wiki.lookup",
        description="внешний инструмент",
        parameters={"type": "object", "properties": {}},
        destructive=False,
        source="mcp:wiki",
        server_name="wiki",
        mcp_tool_name="lookup",
    )
    registry = ResolvedTools(
        specs=[spec], servers={"wiki": ServerEndpoint(url="http://x", api_key_enc=None)}
    )

    outcome = await execute_mcp_tool(spec, {"q": "1"}, tctx, registry)

    assert outcome.decision == "allow"
    assert "недоступен" in outcome.text

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    calls = [r for r in rows if r.action == "agent.tool.mcp"]
    assert len(calls) == 1
    assert calls[0].meta is not None
    assert "ConnectionError" in str(calls[0].meta["error"])


@pytest.mark.asyncio
async def test_execute_mcp_tool_no_endpoint_does_not_crash(
    db_session: AsyncSession,
    test_settings: Settings,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Спецификация без транспорта (ошибка сборки) — текст, не падение."""
    workspace_id, user_id = await _seed_workspace(db_session)
    tctx = await _make_tctx(db_session, test_settings, workspace_id, user_id, None, vector_stores)

    spec = ToolSpec(
        name="ghost.tool",
        description="без транспорта",
        parameters={"type": "object", "properties": {}},
        destructive=False,
        source="mcp:ghost",
        server_name="ghost",
        mcp_tool_name="tool",
    )
    registry = ResolvedTools(specs=[spec])

    outcome = await execute_mcp_tool(spec, {}, tctx, registry)

    assert "временно недоступен" in outcome.text


@pytest.mark.asyncio
async def test_execute_mcp_tool_live_roundtrip(
    db_session: AsyncSession,
    test_settings: Settings,
    live_mcp_server: str,
    vector_stores: list[SQLiteVectorStore],
) -> None:
    """Полный раундтрип: реальный вызов инструмента, дуальный аудит."""
    workspace_id, user_id = await _seed_workspace(db_session)
    tctx = await _make_tctx(db_session, test_settings, workspace_id, user_id, "К0", vector_stores)

    spec = ToolSpec(
        name="demo.echo",
        description="эхо",
        parameters={"type": "object", "properties": {}},
        destructive=False,
        source="mcp:demo",
        server_name="demo",
        mcp_tool_name="echo",
    )
    registry = ResolvedTools(
        specs=[spec],
        servers={"demo": ServerEndpoint(url=live_mcp_server, api_key_enc=None)},
    )

    outcome = await execute_mcp_tool(spec, {"text": "привет"}, tctx, registry)

    assert outcome.decision == "allow"
    assert "echo: привет" in outcome.text

    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    calls = [r for r in rows if r.action == "agent.tool.mcp"]
    assert len(calls) == 1
    assert calls[0].meta is not None
    assert calls[0].meta["decision"] == "allow"
    assert calls[0].meta["server_name"] == "demo"
    # Дуальный аудит: тот же факт виден в спане трассировки.
    tool_spans = [s for s in tctx.trace_ctx.spans if s.name == "agent.tool.demo.echo"]
    assert len(tool_spans) == 1
    assert tool_spans[0].payload["decision"] == "allow"
