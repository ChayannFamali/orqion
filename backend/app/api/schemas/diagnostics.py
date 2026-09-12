"""Схемы диагностики окружения (T-444, T-511, read-only)."""

from __future__ import annotations

from datetime import datetime

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


class DiskDiagnostics(BaseModel):
    """Свободное место для одной точки хранения.

    ``measured_path`` заполнен, только когда измерение выполнено по
    ближайшему существующему предку: на свежей установке каталога
    хранилища ещё нет, но том уже известен.
    """

    label: str
    path: str
    measured_path: str | None = None
    available: bool
    reason: str | None = None
    free_bytes: int | None = None
    total_bytes: int | None = None


class HostDiagnostics(BaseModel):
    """ОС, Python и время работы процесса.

    ``started_at``/``uptime_seconds`` = null, если приложение собрано без
    запуска lifespan (тесты, CLI) — честное «неизвестно», не ноль.
    """

    os_name: str
    os_version: str
    python_version: str
    started_at: datetime | None = None
    uptime_seconds: int | None = None
    disks: list[DiskDiagnostics] = []


class ExternalServiceDiagnostics(BaseModel):
    """Доступность внешнего сервиса по накопленному результату зонда.

    Собственного запроса здесь нет: статус берётся из
    ``Provider.last_probe_at``/``capabilities``, которые пишет периодический
    зонд провайдеров. Отсюда отдельное состояние «ещё не проверялся» —
    это не «недоступен».

    Статуса «отказ» нет намеренно: причина неудачи зонда в БД не
    сохраняется, поэтому «зонд не ответил» и «зонд ответил нулём моделей»
    неразличимы. Оба дают ``no_models`` с пояснением в ``reason``.
    """

    id: str
    # None, когда строки провайдера нет вовсе (эмбеддер не настроен).
    kind: str | None = None
    # Назначение моделей в БД не хранится, поэтому роль определяется по
    # конфигурации: «embedder» — провайдер, через который идут эмбеддинги,
    # остальные — «llm».
    role: str  # "llm" | "embedder"
    base_url: str | None = None
    status: str  # "ok" | "no_models" | "never_probed" | "disabled" | "not_configured"
    last_probe_at: datetime | None = None
    reason: str | None = None
    model_count: int = 0
    available_model_count: int = 0


class LocalComponentDiagnostics(BaseModel):
    """Локальная зависимость или хранилище.

    ``available`` трёхзначно: True/False — факт проверки, None — компонент
    не используется в этой конфигурации, ещё не создан, либо его проверка
    требует сетевого запроса, которого в разделе нет.
    """

    name: str
    available: bool | None = None
    reason: str | None = None
    detail: str | None = None


class EnvironmentDiagnosticsResponse(BaseModel):
    """Ответ GET /api/diagnostics/environment."""

    nvidia: NvidiaDiagnostics
    # Опциональная ссылка на страницу вендора (только чтение; установка
    # драйверов выполняется средствами ОС вне orqion — arch.md §14.3).
    vendor_url: str | None = None
    host: HostDiagnostics
    services: list[ExternalServiceDiagnostics] = []
    components: list[LocalComponentDiagnostics] = []
