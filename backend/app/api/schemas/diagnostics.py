"""Схемы диагностики окружения (T-444, read-only)."""

from __future__ import annotations

from pydantic import BaseModel


class GpuInfo(BaseModel):
    """Метрики одного GPU; поле = null, если метрика не читается."""

    name: str | None = None
    memory_used_mib: int | None = None
    memory_total_mib: int | None = None
    temperature_c: int | None = None
    utilization_percent: int | None = None


class NvidiaDiagnostics(BaseModel):
    """Best-effort чтение через nvidia-smi; без него — честное «недоступно»."""

    available: bool
    reason: str | None = None
    driver_version: str | None = None
    gpus: list[GpuInfo] = []


class EnvironmentDiagnosticsResponse(BaseModel):
    """Ответ GET /api/diagnostics/environment."""

    nvidia: NvidiaDiagnostics
    # Опциональная ссылка на страницу вендора (только чтение; установка
    # драйверов выполняется средствами ОС вне orqion — arch.md §14.3).
    vendor_url: str | None = None
