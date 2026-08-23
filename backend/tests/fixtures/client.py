"""Фикстура тестового клиента с блокировкой сети."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from app.config import Settings, get_or_create_secret_key
from app.db.engine import create_engine, create_session_factory
from app.db.workspace import ensure_default_workspace
from fastapi import FastAPI


@pytest.fixture
def provider_settings(test_settings: Settings) -> Settings:
    """T-430: override embeddings_backend=provider для тестов
    ProviderEmbeddingBackend через реальный lifespan-путь."""
    test_settings.embeddings_backend = "provider"
    test_settings.embeddings_model_alias = "test-embed"
    return test_settings


@pytest_asyncio.fixture
async def app_fixture(test_settings: Settings) -> AsyncIterator[FastAPI]:
    """Создаёт приложение с тестовой БД без запуска миграций.

    Не использует lifespan create_app (который применяет миграции и создаёт
    движок из глобальных настроек). Вместо этого создаёт движок из тестовых
    настроек и инициализирует таблицы напрямую.
    """
    async for app in _build_app(test_settings):
        yield app


@pytest_asyncio.fixture
async def app_provider_fixture(provider_settings: Settings) -> AsyncIterator[FastAPI]:
    """T-430: app_fixture с embeddings_backend=provider.

    Provider+Model (alias=test-embed) должны быть засеяны в БД до
    использования — resolve_embedding_backend ищет их по alias.
    """
    async for app in _build_app(provider_settings):
        yield app


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


@pytest_asyncio.fixture
async def provider_api_client(app_provider_fixture: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """T-430: тестовый клиент для app_provider_fixture."""
    transport = httpx.ASGITransport(app=app_provider_fixture)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        yield client


async def _build_app(settings: Settings) -> AsyncIterator[FastAPI]:
    """Общая логика app_fixture — вызывается с разными settings.

    embeddings_backend=provider → resolve_embedding_backend (реальный
    ProviderEmbeddingBackend через alias-резолв, как в lifespan).
    embeddings_backend=local → AsyncMock (тестам не нужны реальные эмбеддинги).
    """
    from app.main import create_app

    app = create_app()

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    from app.db.base import Base
    from app.db.models import Workspace  # noqa: F401
    from sqlalchemy import text as sa_text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # T-436: FTS5 virtual table — не входит в Base.metadata, создаём явно.
        await conn.execute(
            sa_text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts_messages "
                "USING fts5(content, conversation_id UNINDEXED, message_id UNINDEXED, role UNINDEXED)"
            )
        )

    async with session_factory() as session:
        workspace_id = await ensure_default_workspace(session)
        from app.auth.bootstrap import ensure_builtin_roles

        await ensure_builtin_roles(session, workspace_id)
        from app.router.bootstrap import ensure_default_routing_rules

        await ensure_default_routing_rules(session, workspace_id)
        await session.commit()

    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    app.state.workspace_id = workspace_id

    from pathlib import Path

    from app.auth.rate_limit import LoginRateLimiter
    from app.policy.rate_limiter import RateLimiter

    secret_key = get_or_create_secret_key(settings, Path(settings.blob_store_path))
    app.state.secret_key = secret_key
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter()
    app.state.login_rate_limiter = LoginRateLimiter(
        max_attempts=settings.login_max_attempts,
        period_seconds=settings.login_rate_period_seconds,
    )

    from app.rag.blob import LocalBlobStore

    app.state.blob_store = LocalBlobStore(settings.blob_store_path)

    # T-433: fire-and-forget background tasks (title generation).
    app.state.background_tasks = set()

    from app.rag.vector_store import SQLiteVectorStore

    app.state.vector_store = SQLiteVectorStore(settings.vector_store_path)

    from unittest.mock import AsyncMock, MagicMock

    if settings.embeddings_backend == "provider":
        from app.crypto.service import encrypt_api_key
        from app.db.models import Model, Provider
        from app.rag.embedding_resolver import resolve_embedding_backend

        # Засеваем тестовый Provider+Model с alias=test-embed — чтобы
        # resolve_embedding_backend нашёл alias при конструировании.
        async with session_factory() as session:
            provider = Provider(
                workspace_id=workspace_id,
                kind="openai",
                base_url="http://test-embeddings",
                api_key_enc=encrypt_api_key("sk-test", secret_key),
                enabled=True,
                capabilities={},
            )
            session.add(provider)
            await session.flush()
            model = Model(
                workspace_id=workspace_id,
                provider_id=provider.id,
                alias=settings.embeddings_model_alias,
                upstream_name="text-embedding-3-small",
                locality="external",
                max_input_tokens=8000,
                enabled=True,
            )
            session.add(model)
            await session.commit()

            app.state.embedding_backend = await resolve_embedding_backend(
                settings, session, workspace_id, secret_key
            )
    else:
        embedding_backend = AsyncMock()
        embedding_backend.model_name = MagicMock(return_value="test-embed")
        app.state.embedding_backend = embedding_backend

    yield app

    # T-433: отменяем фоновые задачи (title generation) до dispose —
    # иначе SQLite-сессия остаётся locked.
    for task in app.state.background_tasks:
        task.cancel()
    await asyncio.gather(*app.state.background_tasks, return_exceptions=True)
    app.state.background_tasks.clear()

    await app.state.vector_store.close()
    await engine.dispose()

    from sqlalchemy import create_engine as create_sync_engine

    if settings.database_url.startswith("sqlite"):
        sync_eng = create_sync_engine(settings.database_url)
        with sync_eng.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            Base.metadata.drop_all(conn)
        sync_eng.dispose()
    else:
        url = settings.database_url
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "+psycopg2")
        sync_eng = create_sync_engine(url)
        with sync_eng.begin() as conn:
            table_names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
            if table_names:
                conn.exec_driver_sql(f"TRUNCATE {table_names} CASCADE")
        sync_eng.dispose()
