"""Сборка единого реестра инструментов прогона агента (Т-503).

Единая точка сборки (решение 4 дизайн-ревью и уточнение к нему):
встроенные инструменты и инструменты внешних серверов протокола
собираются в ОДИН список реестра с меткой источника; второй
параллельный список не заводится.

- Неймспейсинг: инструменты внешнего сервера регистрируются под именем
  ``<имя_сервера>.<имя_инструмента>``. Коллизии исключены построением:
  имя сервера уникально в рабочей области и не содержит точки
  (валидация при создании), встроенные инструменты идут без префикса.
- Недоступный сервер (решение 6): его инструменты скрываются из
  прогона, факт пишется в журнал аудита В САМОМ ПУТИ СБОРКИ (не только
  проверяется тестом), состояние видно в диагностике; прогон не падает
  целиком.
- Класс данных К2/К3 (пункт 8 ADR-21 буквально): вынос на внешние
  серверы отклоняется ещё до обнаружения — внешние инструменты не
  входят в реестр вовсе, факт пишется в журнал аудита.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import (
    AGENT_TOOL_SPECS,
    ResolvedTools,
    ServerEndpoint,
    ToolSpec,
    external_tools_allowed,
)
from app.audit.service import write_audit
from app.config import Settings
from app.db.models import McpServer
from app.trace.service import TraceContext, span

_log = logging.getLogger("orqion.mcp.registry")


async def resolve_tools(
    session: AsyncSession,
    settings: Settings,
    secret_key: str,
    workspace_id: str,
    user_id: str,
    trace_ctx: TraceContext,
    conversation_id: str | None,
    corpus_data_class: str | None,
) -> ResolvedTools:
    """Единый реестр инструментов одного прогона.

    Встроенные инструменты входят всегда; внешние — только при классе
    данных ниже К2 и работающем дополнении ``orqion[mcp]``. Сбой
    отдельного сервера не роняет сборку: его инструменты скрываются,
    факт — в журнал аудита.
    """
    registry = ResolvedTools(specs=list(AGENT_TOOL_SPECS))

    # Пункт 8 ADR-21 буквально: вынос данных К2/К3 отклоняется до
    # любого обращения к внешним серверам.
    if not external_tools_allowed(corpus_data_class):
        registry.blocked_external = corpus_data_class
        async with span(
            trace_ctx,
            "mcp.registry.resolve",
            payload={"decision": "blocked_external", "data_class": corpus_data_class},
        ):
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_user_id=user_id,
                action="mcp.tools.blocked",
                object_type="mcp",
                object_id=conversation_id,
                meta={"decision": "deny", "data_class": corpus_data_class},
            )
        _log.info(
            "mcp external tools blocked by data class: user=%s class=%s",
            user_id,
            corpus_data_class,
        )
        return registry

    from app.mcp.client import decrypt_server_connection, discover_tools
    from app.mcp.runtime import is_mcp_available

    if not is_mcp_available():
        # Честная деградация: без дополнения — только встроенные.
        return registry

    result = await session.execute(
        select(McpServer)
        .where(McpServer.workspace_id == workspace_id, McpServer.enabled.is_(True))
        .order_by(McpServer.name)
    )
    servers = list(result.scalars().all())
    if not servers:
        return registry

    for server in servers:
        payload: dict[str, object] = {"server": server.name, "url": server.url}
        try:
            conn = decrypt_server_connection(server.url, server.api_key_enc, secret_key)
            tools = await discover_tools(conn, timeout=settings.mcp_discovery_timeout)
        except Exception as exc:  # noqa: BLE001 недоступность сервера не роняет сборку
            error_text = f"{type(exc).__name__}: {exc}"
            payload["status"] = "unavailable"
            payload["error"] = error_text
            async with span(trace_ctx, "mcp.server.discovery", payload=payload):
                await write_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_user_id=user_id,
                    action="mcp.server.unavailable",
                    object_type="mcp_server",
                    object_id=server.id,
                    meta={"server_name": server.name, "error": error_text},
                )
            _log.warning(
                "mcp server unavailable, tools hidden: name=%s error=%s",
                server.name,
                error_text,
            )
            continue

        added = 0
        for tool in tools:
            if not tool.server_tool_name:
                continue
            registry.specs.append(
                ToolSpec(
                    name=f"{server.name}.{tool.server_tool_name}",
                    description=tool.description or f"Инструмент сервера '{server.name}'",
                    parameters=tool.input_schema or {"type": "object", "properties": {}},
                    # Пункт 9 ревью Т-502 буквально: деструктивность заявляет
                    # сам сервер аннотацией протокола; такой инструмент
                    # останавливает прогон до выполнения и запрашивает
                    # подтверждение пользователя.
                    destructive=tool.destructive,
                    source=f"mcp:{server.name}",
                    server_name=server.name,
                    mcp_tool_name=tool.server_tool_name,
                )
            )
            added += 1
        if added:
            registry.servers[server.name] = ServerEndpoint(
                url=server.url, api_key_enc=server.api_key_enc
            )
        payload["status"] = "ok"
        payload["tools"] = added
        async with span(trace_ctx, "mcp.server.discovery", payload=payload):
            pass

    return registry
