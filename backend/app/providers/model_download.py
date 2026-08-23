"""Скачивание моделей на локальные провайдеры (T-437, часть А).

Единый контракт для Ollama и LM Studio:
- POST /api/providers/{id}/download-models — старт скачивания;
- GET  /api/providers/{id}/download-status/{job_id} — статус (поллинг).

Реализация по провайдерам:
- Ollama: orqion инициирует POST /api/pull и сам читает NDJSON-стрим
  в фоновой asyncio-задаче; прогресс — в in-memory реестре
  (DownloadTracker), job_id — синтетический (uuid).
- LM Studio: нативный download-API (POST /api/v1/models/download →
  GET /api/v1/models/download/status/:job_id) — orqion проксирует,
  job_id выдаёт LM Studio.

Не смешивать с ProviderClient (ADR-6): скачивание — отдельный контракт
и жизненный цикл, не чат-запрос.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.providers import _check_manage_providers
from app.api.schemas.provider import (
    DownloadModelRequest,
    DownloadStatus,
    DownloadStatusResponse,
)
from app.auth.dependencies import current_user
from app.crypto.service import decrypt_api_key
from app.db.models import Provider, User
from app.db.session import get_session
from app.errors import BadRequest, NotFound
from app.providers.client import normalize_base_url

logger = logging.getLogger("orqion.model_download")

DOWNLOADABLE_KINDS = ("ollama", "lmstudio")

# Терминальные задания держатся в реестре ограниченное время — достаточно,
# чтобы клиент дособрал результат; дальше запись выбрасывается при создании
# нового задания.
TERMINAL_JOB_TTL_SECONDS = 1800.0

# Ollama отдаёт прогресс строками по мере скачивания слоёв (может занимать
# минуты на больших моделях) — read-таймаут на строку потока, не на всё
# скачивание.
OLLAMA_PULL_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
LMSTUDIO_TIMEOUT = httpx.Timeout(30.0)


@dataclass
class DownloadJob:
    """Состояние одного скачивания (in-memory, только Ollama)."""

    provider_id: str
    model: str
    status: DownloadStatus = "pending"
    percent: float | None = None
    error: str | None = None
    message: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    task: asyncio.Task[None] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in ("completed", "error", "already_downloaded")


@dataclass
class DownloadStatusView:
    """Единый результат операции скачивания (доменная проекция ответа)."""

    status: DownloadStatus
    job_id: str | None = None
    percent: float | None = None
    error: str | None = None
    message: str | None = None


class DownloadTracker:
    """In-memory реестр заданий скачивания.

    Только для Ollama — orqion сам ведёт задание и хранит его состояние.
    Для LM Studio состояние живёт у провайдера (нативный job_id), orqion
    лишь проксирует статус, в реестр такие задания не попадают.
    """

    def __init__(self) -> None:
        self.jobs: dict[str, DownloadJob] = {}

    def create(self, provider_id: str, model: str) -> tuple[str, DownloadJob]:
        self._prune()
        job_id = uuid.uuid4().hex
        job = DownloadJob(provider_id=provider_id, model=model)
        self.jobs[job_id] = job
        return job_id, job

    def get(self, job_id: str) -> DownloadJob | None:
        return self.jobs.get(job_id)

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [
            job_id
            for job_id, job in self.jobs.items()
            if job.is_terminal and now - job.created_at > TERMINAL_JOB_TTL_SECONDS
        ]
        for job_id in stale:
            del self.jobs[job_id]

    async def cancel_all(self) -> None:
        """Отмена всех активных скачиваний (вызывается при остановке)."""
        tasks = [
            job.task for job in self.jobs.values() if job.task is not None and not job.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _build_client(
    base_url: str,
    api_key: str | None,
    timeout: httpx.Timeout,
) -> httpx.AsyncClient:
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return httpx.AsyncClient(base_url=base_url, timeout=timeout, headers=headers)


async def run_ollama_pull(
    tracker: DownloadTracker,
    job_id: str,
    base_url: str,
    api_key: str | None,
) -> None:
    """Фоновая задача: читает NDJSON-стрим Ollama POST /api/pull до завершения.

    Прогресс — сумма по слоям (у каждого слоя свой digest):
    sum(completed) / sum(total). Строка {"status":"success"} — терминальная;
    строка с ключом "error" — ошибка скачивания.
    """
    job = tracker.get(job_id)
    if job is None:
        return
    job.status = "downloading"
    layers: dict[str, tuple[int, int]] = {}
    try:
        # normalize_base_url: нативные управленческие API живут в корне
        # сервера, без OpenAI-совместимого суффикса /v1.
        async with (
            _build_client(normalize_base_url(base_url), api_key, OLLAMA_PULL_TIMEOUT) as client,
            client.stream(
                "POST",
                "/api/pull",
                json={"model": job.model, "stream": True},
            ) as response,
        ):
            if response.status_code != 200:
                body = (await response.aread()).decode(errors="replace")
                raise RuntimeError(f"Ollama вернул HTTP {response.status_code}: {body[:500]}")
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                error = event.get("error")
                if error:
                    raise RuntimeError(str(error))
                status_text = str(event.get("status") or "")
                if status_text == "success":
                    job.status = "completed"
                    job.percent = 100.0
                    job.message = "success"
                    logger.info(
                        "download_completed",
                        extra={"job_id": job_id, "model": job.model},
                    )
                    return
                digest = event.get("digest")
                total = event.get("total")
                completed = event.get("completed")
                if (
                    isinstance(digest, str)
                    and isinstance(total, int)
                    and isinstance(completed, int)
                ):
                    layers[digest] = (completed, total)
                total_bytes = sum(t for _, t in layers.values())
                if total_bytes > 0:
                    done_bytes = sum(c for c, _ in layers.values())
                    job.percent = round(done_bytes / total_bytes * 100.0, 1)
                if status_text:
                    job.message = status_text
        raise RuntimeError("Ollama закрыл стрим без финального статуса")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — граница системы: фоновая задача не должна падать
        job.status = "error"
        job.error = str(exc)
        logger.warning(
            "download_failed",
            extra={"job_id": job_id, "model": job.model, "error": str(exc)},
        )


def _lmstudio_error_text(error: Any) -> str:
    """Нативный error LM Studio бывает строкой или объектом {type, message}."""
    if isinstance(error, dict):
        message = error.get("message") or error.get("type")
        if message:
            return str(message)
    return str(error)


def _lmstudio_error(data: dict[str, Any]) -> str:
    error = data.get("error")
    return _lmstudio_error_text(error) if error else "LM Studio вернул статус failed"


def _lmstudio_percent(data: dict[str, Any]) -> float | None:
    total = data.get("total_size_bytes")
    done = data.get("downloaded_bytes")
    if isinstance(total, int) and isinstance(done, int) and total > 0:
        return round(done / total * 100.0, 1)
    return None


async def _lmstudio_request(
    base_url: str,
    api_key: str | None,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> DownloadStatusView | tuple[int, str]:
    """Один запрос к LM Studio. Возвращает (статус-код, тело) либо готовую ошибку."""
    try:
        async with _build_client(base_url, api_key, LMSTUDIO_TIMEOUT) as client:
            response = await client.request(method, path, json=payload)
            return response.status_code, response.text
    except httpx.HTTPError as exc:
        return DownloadStatusView(status="error", error=f"LM Studio недоступен: {exc}")


def _lmstudio_parse(
    result: tuple[int, str], job_id: str | None
) -> DownloadStatusView | dict[str, Any]:
    status_code, body_text = result
    if status_code != 200:
        return DownloadStatusView(
            job_id=job_id,
            status="error",
            error=f"LM Studio вернул HTTP {status_code}: {body_text[:500]}",
        )
    try:
        data = json.loads(body_text)
    except json.JSONDecodeError:
        return DownloadStatusView(
            job_id=job_id, status="error", error="Некорректный JSON в ответе LM Studio"
        )
    if not isinstance(data, dict):
        return DownloadStatusView(
            job_id=job_id, status="error", error="Некорректный ответ LM Studio"
        )
    # LM Studio отвечает 200 с {"error": ...} на нераспознанные пути
    # (без ключа "status") — выносим как ошибку, не как неизвестный статус.
    if "status" not in data and data.get("error"):
        return DownloadStatusView(
            job_id=job_id, status="error", error=_lmstudio_error_text(data["error"])
        )
    return data


async def lmstudio_start_download(
    base_url: str,
    api_key: str | None,
    model: str,
) -> DownloadStatusView:
    """POST /api/v1/models/download — старт; возврат в едином контракте.

    Нативные статусы: downloading | paused | completed | failed |
    already_downloaded. already_downloaded приходит без job_id и
    терминален сразу — поллинг не нужен.
    """
    result = await _lmstudio_request(
        normalize_base_url(base_url),
        api_key,
        "POST",
        "/api/v1/models/download",
        {"model": model},
    )
    if isinstance(result, DownloadStatusView):
        return result
    parsed = _lmstudio_parse(result, job_id=None)
    if isinstance(parsed, DownloadStatusView):
        return parsed
    data = parsed

    native_status = str(data.get("status") or "")
    if native_status == "already_downloaded":
        return DownloadStatusView(status="already_downloaded", percent=100.0)

    raw_job_id = data.get("job_id")
    job_id = raw_job_id if isinstance(raw_job_id, str) and raw_job_id else None

    if native_status == "completed":
        return DownloadStatusView(job_id=job_id, status="completed", percent=100.0)
    if native_status == "failed":
        return DownloadStatusView(job_id=job_id, status="error", error=_lmstudio_error(data))
    if native_status in ("downloading", "paused"):
        if job_id is None:
            # Нативный контракт не сошёлся — явная ошибка, не workaround.
            return DownloadStatusView(
                status="error",
                error="LM Studio не вернул job_id для активного скачивания",
            )
        return DownloadStatusView(job_id=job_id, status="downloading", message=native_status)
    return DownloadStatusView(
        job_id=job_id,
        status="error",
        error=f"Неизвестный статус LM Studio: {native_status or '<пусто>'}",
    )


async def lmstudio_download_status(
    base_url: str,
    api_key: str | None,
    job_id: str,
) -> DownloadStatusView:
    """GET /api/v1/models/download/status/:job_id — форвард статуса."""
    result = await _lmstudio_request(
        normalize_base_url(base_url),
        api_key,
        "GET",
        f"/api/v1/models/download/status/{job_id}",
        None,
    )
    if isinstance(result, DownloadStatusView):
        result.job_id = job_id
        return result
    parsed = _lmstudio_parse(result, job_id=job_id)
    if isinstance(parsed, DownloadStatusView):
        return parsed
    data = parsed

    native_status = str(data.get("status") or "")
    percent = _lmstudio_percent(data)
    if native_status == "completed":
        return DownloadStatusView(job_id=job_id, status="completed", percent=100.0)
    if native_status == "failed":
        return DownloadStatusView(job_id=job_id, status="error", error=_lmstudio_error(data))
    if native_status in ("downloading", "paused"):
        return DownloadStatusView(
            job_id=job_id, status="downloading", percent=percent, message=native_status
        )
    return DownloadStatusView(
        job_id=job_id,
        status="error",
        error=f"Неизвестный статус LM Studio: {native_status or '<пусто>'}",
    )


# ---------------------------------------------------------------------------
# API-роуты: единый контракт (старт + поллинг) для обоих провайдеров
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/api/providers", tags=["providers"], dependencies=[Depends(current_user)]
)


async def _downloadable_provider(
    session: AsyncSession,
    user: User,
    provider_id: str,
    workspace_id: str,
) -> Provider:
    """Провайдер с правом управления и kind из скачиваемого набора."""
    if not await _check_manage_providers(session, user):
        raise NotFound(
            constraint={"object": "providers", "reason": "manage_providers required"},
            hint="Нет права на управление провайдерами",
        )
    result = await session.execute(
        select(Provider).where(
            Provider.id == provider_id,
            Provider.workspace_id == workspace_id,
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise NotFound(
            constraint={"object": "provider", "id": provider_id},
            hint="Провайдер не найден",
        )
    if provider.kind not in DOWNLOADABLE_KINDS:
        raise BadRequest(
            "Скачивание моделей доступно только для локальных провайдеров",
            constraint={"provider_kind": provider.kind},
            hint="Операция поддерживается для провайдеров типа ollama и lmstudio",
        )
    return provider


def _provider_api_key(provider: Provider, secret_key: str) -> str | None:
    if not provider.api_key_enc:
        return None
    return decrypt_api_key(provider.api_key_enc, secret_key)


def _tracker(request: Request) -> DownloadTracker:
    return request.app.state.download_tracker  # type: ignore[no-any-return]


def _view_to_response(view: DownloadStatusView) -> DownloadStatusResponse:
    return DownloadStatusResponse(
        job_id=view.job_id,
        status=view.status,
        percent=view.percent,
        error=view.error,
        message=view.message,
    )


@router.post(
    "/{provider_id}/download-models",
    response_model=DownloadStatusResponse,
    status_code=202,
)
async def start_model_download(
    provider_id: str,
    body: DownloadModelRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> DownloadStatusResponse:
    """Старт скачивания модели на локальный провайдер.

    202 — задание принято, дальше клиент поллит статус.
    200 — терминальный статус сразу (уже скачана, скачана синхронно,
    либо ошибка старта).
    """
    provider = await _downloadable_provider(
        session, user, provider_id, request.app.state.workspace_id
    )
    secret_key: str = request.app.state.secret_key
    api_key = _provider_api_key(provider, secret_key)

    if provider.kind == "ollama":
        tracker = _tracker(request)
        job_id, job = tracker.create(provider.id, body.model)
        job.task = asyncio.create_task(run_ollama_pull(tracker, job_id, provider.base_url, api_key))
        logger.info(
            "download_started",
            extra={
                "job_id": job_id,
                "provider_id": provider.id,
                "provider_kind": provider.kind,
                "model": body.model,
            },
        )
        return DownloadStatusResponse(job_id=job_id, status="pending")

    view = await lmstudio_start_download(provider.base_url, api_key, body.model)
    response.status_code = 202 if view.status in ("pending", "downloading") else 200
    return _view_to_response(view)


@router.get(
    "/{provider_id}/download-status/{job_id}",
    response_model=DownloadStatusResponse,
)
async def get_model_download_status(
    provider_id: str,
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> DownloadStatusResponse:
    """Статус скачивания. Клиент поллит до терминального статуса."""
    provider = await _downloadable_provider(
        session, user, provider_id, request.app.state.workspace_id
    )
    secret_key: str = request.app.state.secret_key
    api_key = _provider_api_key(provider, secret_key)

    if provider.kind == "ollama":
        tracker = _tracker(request)
        job = tracker.get(job_id)
        if job is None or job.provider_id != provider.id:
            raise NotFound(
                constraint={"object": "download_job", "id": job_id},
                hint="Задание скачивания не найдено",
            )
        return DownloadStatusResponse(
            job_id=job_id,
            status=job.status,
            percent=job.percent,
            error=job.error,
            message=job.message,
        )

    view = await lmstudio_download_status(provider.base_url, api_key, job_id)
    return _view_to_response(view)
