"""Фабрики тестовых объектов."""

from __future__ import annotations

from app.db.models import Workspace
from sqlalchemy.ext.asyncio import AsyncSession


async def make_workspace(session: AsyncSession, name: str = "test") -> Workspace:
    """Создаёт и возвращает тестовый workspace."""
    ws = Workspace(name=name)
    session.add(ws)
    await session.flush()
    return ws
