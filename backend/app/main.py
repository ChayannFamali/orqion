"""Точка входа: приложение FastAPI, GET /health, отдача статики."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.bootstrap import ensure_initial_admin
from app.config import Settings, get_or_create_secret_key
from app.db.engine import create_engine, create_session_factory
from app.db.workspace import ensure_default_workspace
from app.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    setup_logging(settings.log_level)

    data_dir = Path(settings.blob_store_path).parent
    data_dir.mkdir(parents=True, exist_ok=True)
    get_or_create_secret_key(settings, data_dir)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory

    from app.db.migrate import run_migrations_sync

    await asyncio.to_thread(run_migrations_sync, settings.database_url)

    async with session_factory() as session:
        workspace_id = await ensure_default_workspace(session)
        await ensure_initial_admin(session, workspace_id)
        await session.commit()
    app.state.workspace_id = workspace_id

    yield

    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="orqion",
        lifespan=lifespan,
    )

    from app.api.exception_handlers import register_exception_handlers

    register_exception_handlers(app)

    from app.api.health import router as health_router
    from app.api.routes import auth_router

    app.include_router(health_router)
    app.include_router(auth_router)

    _mount_static(app)

    return app


def _mount_static(app: FastAPI) -> None:
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")


app = create_app()
