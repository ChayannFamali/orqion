"""Точка входа: приложение FastAPI, GET /health, отдача статики."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth.bootstrap import ensure_initial_admin
from app.config import Settings, get_or_create_secret_key
from app.db.engine import create_engine, create_session_factory
from app.db.workspace import ensure_default_workspace
from app.logging import setup_logging
from app.policy.rate_limiter import RateLimiter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    setup_logging(settings.log_level)
    app.state.settings = settings

    data_dir = Path(settings.blob_store_path).parent
    data_dir.mkdir(parents=True, exist_ok=True)
    secret_key = get_or_create_secret_key(settings, data_dir)
    app.state.secret_key = secret_key

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory

    from app.db.migrate import run_migrations_sync

    await asyncio.to_thread(run_migrations_sync, settings.database_url)

    async with session_factory() as session:
        workspace_id = await ensure_default_workspace(session)
        await ensure_initial_admin(session, workspace_id)
        from app.router.bootstrap import ensure_default_routing_rules

        await ensure_default_routing_rules(session, workspace_id)
        await session.commit()
    app.state.workspace_id = workspace_id
    app.state.rate_limiter = RateLimiter()
    from app.auth.rate_limit import LoginRateLimiter

    app.state.login_rate_limiter = LoginRateLimiter(
        max_attempts=settings.login_max_attempts,
        period_seconds=settings.login_rate_period_seconds,
    )

    from app.rag.blob import LocalBlobStore

    if settings.blob_store_backend == "s3":
        from app.rag.s3 import S3BlobStore

        app.state.blob_store = S3BlobStore(
            endpoint_url=settings.s3_endpoint_url,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        )
    else:
        app.state.blob_store = LocalBlobStore(settings.blob_store_path)

    from app.rag.vector_store import SQLiteVectorStore

    if settings.vector_store == "qdrant":
        from app.rag.qdrant_store import QdrantVectorStore

        app.state.vector_store = QdrantVectorStore(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
    else:
        app.state.vector_store = SQLiteVectorStore(settings.vector_store_path)

    # T-433: fire-and-forget background tasks (title generation).
    # Task-референс сохраняется здесь, чтобы Python не собрать GC до завершения.
    app.state.background_tasks = set[asyncio.Task[None]]()

    # T-437: in-memory реестр заданий скачивания моделей (Ollama pull).
    from app.providers.model_download import DownloadTracker

    app.state.download_tracker = DownloadTracker()

    # Embedding backend для RAG-конвейера (T-221, T-430)
    from app.rag.embedding_resolver import resolve_embedding_backend

    async with session_factory() as session:
        app.state.embedding_backend = await resolve_embedding_backend(
            settings, session, workspace_id, secret_key
        )

    # T-431: recovery осиротевших index_version в статусе "building".
    await recover_stale_building_versions(session_factory, workspace_id, settings)

    # Ресурсы, требующие close() при остановке, регистрируются в AsyncExitStack.
    # blob_store (LocalBlobStore/S3BlobStore) не имеет close() — нет открытых
    # соединений (LocalBlobStore — файлы, S3BlobStore — контекстный менеджер
    # per-operation через aioboto3).
    cleanup = AsyncExitStack()
    if hasattr(app.state.vector_store, "close"):
        cleanup.push_async_callback(app.state.vector_store.close)

    from app.providers.probe_scheduler import probe_scheduler
    from app.usage.scheduler import aggregate_scheduler

    # T-407: init Prometheus metrics registry if enabled
    if settings.metrics_enabled:
        from app.metrics.registry import init_metrics

        init_metrics()

    probe_task = asyncio.create_task(
        probe_scheduler(
            session_factory,
            secret_key,
            settings.probe_interval_seconds,
        )
    )
    aggregate_task = asyncio.create_task(
        aggregate_scheduler(session_factory, workspace_id, settings)
    )

    # OIDC sync scheduler (T-405) — только если oidc_sync_enabled
    oidc_sync_task: asyncio.Task[None] | None = None
    if settings.oidc_sync_enabled:
        from app.auth.oidc_sync_scheduler import oidc_sync_scheduler

        oidc_sync_task = asyncio.create_task(
            oidc_sync_scheduler(session_factory, settings, secret_key, workspace_id)
        )

    # Retention scheduler (T-406) — всегда запущен, no-op если все retention=0
    from app.retention.scheduler import retention_scheduler

    retention_task = asyncio.create_task(
        retention_scheduler(session_factory, settings, workspace_id)
    )

    yield

    probe_task.cancel()
    aggregate_task.cancel()
    if oidc_sync_task is not None:
        oidc_sync_task.cancel()
    retention_task.cancel()
    try:
        await probe_task
    except asyncio.CancelledError:
        pass
    try:
        await aggregate_task
    except asyncio.CancelledError:
        pass
    if oidc_sync_task is not None:
        try:
            await oidc_sync_task
        except asyncio.CancelledError:
            pass
    try:
        await retention_task
    except asyncio.CancelledError:
        pass

    # T-437: отмена фоновых скачиваний до закрытия ресурсов.
    await app.state.download_tracker.cancel_all()

    # Закрытие ресурсов: vector_store.close() и др.
    # AsyncExitStack гарантирует, что исключение при закрытии одного
    # ресурса не мешает закрыть остальные.
    await cleanup.aclose()

    await engine.dispose()


def create_app() -> FastAPI:
    import os

    debug = os.environ.get("ORQION_DEBUG", "").lower() in ("1", "true", "yes")
    app = FastAPI(
        title="orqion",
        lifespan=lifespan,
        debug=debug,
    )

    from app.api.exception_handlers import register_exception_handlers

    register_exception_handlers(app)

    from app.api.health import router as health_router
    from app.api.routes import (
        agent_router,
        analytics_router,
        audit_router,
        auth_router,
        chat_router,
        code_graph_router,
        config_router,
        conversations_router,
        corpora_router,
        diagnostics_router,
        document_graph_router,
        document_router,
        documents_router,
        eval_router,
        index_versions_router,
        models_router,
        prompt_templates_router,
        providers_router,
        rag_settings_router,
        roles_router,
        routing_router,
        traces_router,
        users_router,
    )

    # T-437: скачивание моделей (роутер живёт в app.providers — доменный модуль)
    from app.providers.model_download import router as model_download_router

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(agent_router)
    app.include_router(analytics_router)
    app.include_router(audit_router)
    app.include_router(chat_router)
    app.include_router(code_graph_router)
    app.include_router(config_router)
    app.include_router(conversations_router)
    app.include_router(corpora_router)
    app.include_router(diagnostics_router)
    app.include_router(document_graph_router)
    app.include_router(documents_router)
    app.include_router(document_router)
    app.include_router(eval_router)
    app.include_router(index_versions_router)
    app.include_router(models_router)
    app.include_router(providers_router)
    app.include_router(model_download_router)
    app.include_router(prompt_templates_router)
    app.include_router(rag_settings_router)
    app.include_router(roles_router)
    app.include_router(routing_router)
    app.include_router(traces_router)
    app.include_router(users_router)

    # T-407: /metrics endpoint — только если metrics_enabled
    from app.config import Settings

    if Settings().metrics_enabled:
        from app.api.metrics import router as metrics_router

        app.include_router(metrics_router)

    _mount_static(app)

    return app


def _mount_static(app: FastAPI) -> None:
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")


from datetime import UTC

from sqlalchemy.ext.asyncio import AsyncSession


async def recover_stale_building_versions(
    session_factory: Callable[[], AsyncSession],
    workspace_id: str,
    settings: Settings,
) -> None:
    """T-431: перевод осиротевших index_version(status=building) в interrupted.

    На single-process (ADR-1) любая строка в "building" при старте
    гарантированно осиротела — процесс, державший фоновую задачу T-214,
    умер (раз мы сейчас стартуем заново). Порог (5 минут по умолчанию) —
    defensive margin на будущее (multi-worker §14.2 пока не поддерживается),
    не текущая необходимость: на single-process все "building" старше порога
    — но порог страхует от ложного срабатывания при быстром рестарте внутри
    одного цикла build.

    Активная версия корпуса (corpus.active_index_version_id) не затронута —
    она имеет status="active", не "building".
    """
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from app.db.models import IndexVersion

    stale_threshold = datetime.now(UTC) - timedelta(minutes=settings.index_building_stale_minutes)
    async with session_factory() as session:
        stmt = (
            update(IndexVersion)
            .where(
                IndexVersion.workspace_id == workspace_id,
                IndexVersion.status == "building",
                IndexVersion.created_at < stale_threshold,
            )
            .values(status="interrupted")
        )
        result = await session.execute(stmt)
        rowcount = result.rowcount if hasattr(result, "rowcount") else 0
        if rowcount > 0:
            import logging

            logging.getLogger("orqion.lifespan").info(
                "Recovered %d stale index_version(s) in building state "
                "(workspace=%s, threshold=%s minutes)",
                rowcount,
                workspace_id,
                settings.index_building_stale_minutes,
            )
        await session.commit()


app = create_app()
