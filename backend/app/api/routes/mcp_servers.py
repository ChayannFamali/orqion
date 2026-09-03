"""CRUD реестра серверов протокола передачи контекста моделям (Т-503).

Решения дизайн-ревью / пункта 8 ADR-21:

- реестр ведёт только администратор (способность ``manage_mcp_servers``
  по образцу ``manage_providers``; без права — 404, существование
  раздела не раскрывается);
- транспорт — только HTTP к явному адресу: сервер хранит URL, локальные
  процессы не запускаются;
- секреты — тот же механизм шифрования, что у ключей провайдеров
  (``api_key_enc``, AES-GCM); в ответах не возвращаются;
- имя сервера — неймспейс его инструментов в едином реестре, поэтому
  после создания не меняется (уточнение к решению 4).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.mcp import (
    McpServerCreate,
    McpServerDeleteResponse,
    McpServerListResponse,
    McpServerResponse,
    McpServerUpdate,
)
from app.auth.dependencies import current_user
from app.crypto.service import encrypt_api_key
from app.db.models import McpServer, User
from app.db.session import get_session
from app.errors import Conflict, NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(prefix="/api/mcp-servers", tags=["mcp"], dependencies=[Depends(current_user)])


async def _check_manage_mcp_servers(session: AsyncSession, user: User) -> bool:
    """True если admin (через *). Иначе — NotFound (не раскрываем существование)."""
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities or "manage_mcp_servers" in policy.capabilities


def _server_to_response(server: McpServer) -> McpServerResponse:
    return McpServerResponse(
        id=server.id,
        name=server.name,
        url=server.url,
        enabled=server.enabled,
        has_api_key=server.api_key_enc is not None,
    )


@router.post("", response_model=McpServerResponse, status_code=201)
async def create_mcp_server(
    body: McpServerCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> McpServerResponse:
    if not await _check_manage_mcp_servers(session, user):
        raise NotFound(
            constraint={"object": "mcp-servers", "reason": "manage_mcp_servers required"},
            hint="Нет права на управление серверами инструментов",
        )

    secret_key = request.app.state.secret_key
    api_key_enc = None
    if body.api_key:
        api_key_enc = encrypt_api_key(body.api_key, secret_key)

    server = McpServer(
        workspace_id=request.app.state.workspace_id,
        name=body.name,
        url=body.url,
        api_key_enc=api_key_enc,
        enabled=body.enabled,
    )
    session.add(server)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(
            "Сервер с таким именем уже зарегистрирован",
            constraint={"name": body.name},
            hint="Имя уникально в рабочей области — оно служит неймспейсом инструментов",
        ) from exc

    return _server_to_response(server)


@router.get("", response_model=McpServerListResponse)
async def list_mcp_servers(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> McpServerListResponse:
    if not await _check_manage_mcp_servers(session, user):
        raise NotFound(
            constraint={"object": "mcp-servers", "reason": "manage_mcp_servers required"},
            hint="Нет права на управление серверами инструментов",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(McpServer).where(McpServer.workspace_id == workspace_id).order_by(McpServer.name)
    )
    servers = result.scalars().all()
    return McpServerListResponse(servers=[_server_to_response(s) for s in servers])


@router.patch("/{server_id}", response_model=McpServerResponse)
async def update_mcp_server(
    server_id: str,
    body: McpServerUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> McpServerResponse:
    if not await _check_manage_mcp_servers(session, user):
        raise NotFound(
            constraint={"object": "mcp-servers", "reason": "manage_mcp_servers required"},
            hint="Нет права на управление серверами инструментов",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(McpServer).where(McpServer.id == server_id, McpServer.workspace_id == workspace_id)
    )
    server = result.scalar_one_or_none()
    if server is None:
        raise NotFound(
            constraint={"object": "mcp_server", "id": server_id},
            hint="Сервер не найден",
        )

    if body.url is not None:
        server.url = body.url
    if body.api_key is not None:
        secret_key = request.app.state.secret_key
        server.api_key_enc = encrypt_api_key(body.api_key, secret_key)
    if body.enabled is not None:
        server.enabled = body.enabled

    await session.commit()
    return _server_to_response(server)


@router.delete("/{server_id}", response_model=McpServerDeleteResponse)
async def delete_mcp_server(
    server_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> McpServerDeleteResponse:
    """Удаление сервера из реестра.

    Исторических ссылок на сервер нет: аудит вызовов хранит имя
    инструмента текстом (журнал переживает удаление первичных данных,
    §5.3). Основной путь «временно выключить» — PATCH enabled=false.
    """
    if not await _check_manage_mcp_servers(session, user):
        raise NotFound(
            constraint={"object": "mcp-servers", "reason": "manage_mcp_servers required"},
            hint="Нет права на управление серверами инструментов",
        )

    workspace_id = request.app.state.workspace_id
    result = await session.execute(
        select(McpServer).where(McpServer.id == server_id, McpServer.workspace_id == workspace_id)
    )
    server = result.scalar_one_or_none()
    if server is None:
        raise NotFound(
            constraint={"object": "mcp_server", "id": server_id},
            hint="Сервер не найден",
        )

    await session.delete(server)
    await session.commit()
    return McpServerDeleteResponse(deleted=True)
