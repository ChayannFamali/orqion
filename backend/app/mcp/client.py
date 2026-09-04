"""Транспорт клиента протокола: подключение и вызов инструмента (Т-503).

Транспорт — только HTTP к явному адресу (решение 1 дизайн-ревью /
пункт 8 ADR-21): ни локальных процессов, ни произвольных схем. Каждый
вызов открывает собственную сессию и закрывает её по выходе — прогон
синхронный в одном запросе (решение 2), долгоживущих подключений нет.

Все импорты ``mcp`` — ленивые, внутри функций: без дополнения
``orqion[mcp]`` модуль импортируется, а вызовы поднимают
``ImportError`` с подсказкой установки (паттерн Т-444/Т-502/Т-505).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.crypto.service import decrypt_api_key


@dataclass(frozen=True)
class ServerConnection:
    """Точка подключения к серверу (адрес и расшифрованный секрет)."""

    url: str
    api_key: str | None


@dataclass(frozen=True)
class DiscoveredTool:
    """Инструмент, отданный сервером при обнаружении."""

    server_tool_name: str
    description: str
    input_schema: dict[str, object]
    # Флаг деструктивности приходит из протокола (аннотация
    # ``destructiveHint``), а не из настроек: сервер сам заявляет, что
    # инструмент меняет внешний мир.
    destructive: bool = False


@dataclass(frozen=True)
class ToolCallResult:
    """Результат вызова инструмента сервера."""

    text: str
    is_error: bool


def connection_headers(conn: ServerConnection) -> dict[str, str] | None:
    """Заголовки авторизации для транспорта, если секрет задан."""
    if conn.api_key:
        return {"Authorization": f"Bearer {conn.api_key}"}
    return None


async def discover_tools(conn: ServerConnection, timeout: float) -> list[DiscoveredTool]:
    """Подключиться к серверу и получить список инструментов.

    Возбуждает исключения транспорта при недоступности сервера —
    вызывающий код превращает их в факт «инструменты скрыты, запись в
    аудит» (решение 6).
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with (
        streamablehttp_client(conn.url, headers=connection_headers(conn), timeout=timeout) as (
            read_stream,
            write_stream,
            _,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.list_tools()
    tools: list[DiscoveredTool] = []
    for t in result.tools:
        schema = t.inputSchema if isinstance(t.inputSchema, dict) else {}
        annotations = t.annotations
        destructive = bool(getattr(annotations, "destructiveHint", False) if annotations else False)
        tools.append(
            DiscoveredTool(
                server_tool_name=t.name,
                description=t.description or "",
                input_schema=schema,
                destructive=destructive,
            )
        )
    return tools


async def call_tool(
    conn: ServerConnection,
    tool_name: str,
    arguments: dict[str, object],
    timeout: float,
) -> ToolCallResult:
    """Один вызов инструмента сервера; сессия закрывается по выходе."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with (
        streamablehttp_client(conn.url, headers=connection_headers(conn), timeout=timeout) as (
            read_stream,
            write_stream,
            _,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        result = await session.call_tool(tool_name, arguments)

    parts: list[str] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return ToolCallResult(text="\n".join(parts), is_error=bool(result.isError))


def decrypt_server_connection(
    url: str, api_key_enc: str | None, secret_key: str
) -> ServerConnection:
    """Секрет сервера расшифровывается тем же механизмом, что ключи провайдеров."""
    api_key = decrypt_api_key(api_key_enc, secret_key) if api_key_enc else None
    return ServerConnection(url=url, api_key=api_key)
