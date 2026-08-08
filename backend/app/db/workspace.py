"""Зависимость workspace_id: создание по умолчанию и доступ в роутерах."""

from __future__ import annotations

import logging

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Workspace

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_NAME = "default"


async def ensure_default_workspace(session: AsyncSession) -> str:
    """Создаёт единственную запись workspace при первом старте.

    Идемпотентно: если запись существует — возвращает её ID.
    """
    result = await session.execute(select(Workspace).limit(1))
    ws = result.scalar_one_or_none()
    if ws is None:
        ws = Workspace(name=DEFAULT_WORKSPACE_NAME)
        session.add(ws)
        await session.flush()
        logger.info(
            "workspace_created",
            extra={"workspace_id": ws.id},
        )
    return ws.id


async def get_workspace_id(request: Request) -> str:
    """Зависимость FastAPI: возвращает ID единственного workspace."""
    workspace_id: str = request.app.state.workspace_id
    return workspace_id
