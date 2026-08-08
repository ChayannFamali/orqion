"""Фикстура тестового клиента с блокировкой сети."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from app.config import Settings
from app.db.engine import create_engine, create_session_factory
from app.db.workspace import ensure_default_workspace
from fastapi import FastAPI


@pytest_asyncio.fixture
async def app_fixture(test_settings: Settings) -> AsyncIterator[FastAPI]:
    """Создаёт приложение с тестовой БД без запуска миграций.

    Не использует lifespan create_app (который применяет миграции и создаёт
    движок из глобальных настроек). Вместо этого создаёт движок из тестовых
    настроек и инициализирует таблицы напрямую.
    """
    from app.main import create_app

    app = create_app()

    engine = create_engine(test_settings)
    session_factory = create_session_factory(engine)

    from app.db.base import Base
    from app.db.models import Workspace  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        workspace_id = await ensure_default_workspace(session)
        await session.commit()

    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    app.state.workspace_id = workspace_id

    yield app

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def api_client(app_fixture: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Тестовый клиент с ASGI-транспортом.

    Блокирует любые реальные сетевые обращения: используется ASGITransport,
    который обрабатывает запросы в процессе.
    """
    transport = httpx.ASGITransport(app=app_fixture)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client
