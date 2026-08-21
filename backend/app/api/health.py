"""GET /health — проверка живости. GET /ready — проверка готовности зависимостей."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"status": "ok"},
    )


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Проверка готовности зависимостей: DB, vector store, blob store.

    Возвращает 200 {"status":"ready"} если все три доступны.
    Возвращает 503 {"status":"not_ready","failures":[...]} с перечнем отказов.
    """
    failures: list[dict[str, str]] = []

    # DB: SELECT 1
    try:
        engine = request.app.state.db_engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — readiness check must report any failure
        failures.append({"component": "database", "reason": str(exc)})

    # Vector store: no-op check
    try:
        vector_store = request.app.state.vector_store
        if hasattr(vector_store, "_get_conn"):
            await vector_store._get_conn()
        elif hasattr(vector_store, "_ensure_collection"):
            await vector_store._ensure_collection()
        else:
            # Unknown vector store — assume ready
            pass
    except Exception as exc:  # noqa: BLE001 — readiness check must report any failure
        failures.append({"component": "vector_store", "reason": str(exc)})

    # Blob store: root directory / bucket check
    try:
        blob_store = request.app.state.blob_store
        if hasattr(blob_store, "_root"):
            from pathlib import Path

            root = Path(blob_store._root)
            if not root.exists():
                raise FileNotFoundError(f"Blob store root not found: {root}")
        elif hasattr(blob_store, "_bucket"):
            # S3: light head_bucket via _ensure_bucket
            await blob_store._ensure_bucket()
        else:
            pass
    except Exception as exc:  # noqa: BLE001 — readiness check must report any failure
        failures.append({"component": "blob_store", "reason": str(exc)})

    if failures:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "failures": failures},
        )

    return JSONResponse(
        status_code=200,
        content={"status": "ready"},
    )
