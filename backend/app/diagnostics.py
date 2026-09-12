"""T-444, T-511: диагностика окружения хоста — только чтение, best-effort.

GPU: метрики через nvidia-smi (shutil.which + subprocess; pynvml не
используется — покрытие то же, а новая зависимость потребовала бы
отдельного согласования). Инструмент не найден или вызов не удался —
честный статус «недоступно», без падения и без предположений.

Хост: ОС, версия Python, время работы процесса и свободное место на томах,
где лежат хранилища.

Внешние сервисы: **собственного сетевого запроса раздел не делает**. Статус
берётся из результата периодического зонда провайдеров, который уже лежит в
БД (``Provider.last_probe_at`` + ``capabilities``). Отсюда отдельное
состояние «ещё не проверялся»: зонд спит перед первым прогоном, поэтому на
свежей установке результата нет в течение интервала зондирования — это не
«недоступен».

Локальные компоненты: наличие пакетов и файлов хранилищ. Проверка пакета —
``importlib.util.find_spec``, без импорта и тем более без загрузки весов:
``create_reranker()`` конструирует модель целиком, для диагностики это
недопустимо по стоимости. Поэтому «пакет установлен» — не то же самое, что
«веса загружены», и в ``detail`` это сказано явно.

Read-only намеренно (arch.md §14.3): orqion не управляет драйверами и
backend'ом инференса — никаких действий по установке/обновлению здесь
нет и не будет.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import platform
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas.diagnostics import (
    DiskDiagnostics,
    EnvironmentDiagnosticsResponse,
    ExternalServiceDiagnostics,
    GpuInfo,
    HostDiagnostics,
    LocalComponentDiagnostics,
    NvidiaDiagnostics,
)
from app.config import Settings
from app.db.models import Model, Provider
from app.rag.vector_store import sqlite_vec_status

logger = logging.getLogger("orqion.diagnostics")

NVIDIA_SMI_TIMEOUT_SECONDS = 5.0
NVIDIA_VENDOR_URL = "https://www.nvidia.com/en-us/drivers/"
#: Пакет локальных эмбеддингов и реранкера (extras [full]).
EMBEDDING_PACKAGE = "FlagEmbedding"

_QUERY_FIELDS = "driver_version,name,memory.used,memory.total,temperature.gpu,utilization.gpu"


async def _run_nvidia_smi_query() -> str | None:
    """Сырой CSV-вывод nvidia-smi или None, если инструмент недоступен."""
    tool = shutil.which("nvidia-smi")
    if tool is None:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            tool,
            f"--query-gpu={_QUERY_FIELDS}",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=NVIDIA_SMI_TIMEOUT_SECONDS)
    except (OSError, TimeoutError):
        logger.warning("nvidia-smi: вызов не удался", exc_info=True)
        return None
    if proc.returncode != 0:
        return None
    return stdout.decode("utf-8", errors="replace")


def _int_or_none(cell: str | None) -> int | None:
    if cell is None:
        return None
    try:
        return int(cell.strip())
    except ValueError:
        return None


def _parse_gpu_row(row: str) -> tuple[str | None, GpuInfo] | None:
    """Строка CSV: driver_version, name, mem.used, mem.total, temp, util."""
    parts = [p.strip() for p in row.split(",")]
    if len(parts) != 6:
        return None
    # "[N/A]" — штатный маркер недоступной метрики nvidia-smi
    cells = [None if p == "[N/A]" else p for p in parts]
    return cells[0], GpuInfo(
        name=cells[1],
        memory_used_mib=_int_or_none(cells[2]),
        memory_total_mib=_int_or_none(cells[3]),
        temperature_c=_int_or_none(cells[4]),
        utilization_percent=_int_or_none(cells[5]),
    )


def _nvidia_section(raw: str | None) -> tuple[NvidiaDiagnostics, str | None]:
    """Секция GPU: (диагностика, ссылка на страницу вендора).

    Ссылка возвращается только при успешном чтении: предлагать скачать
    драйвер, когда метрики и так читаются, бессмысленно.
    """
    if raw is None:
        return (
            NvidiaDiagnostics(available=False, reason="nvidia-smi не найден или недоступен"),
            None,
        )

    rows = [line.strip() for line in raw.splitlines() if line.strip()]
    parsed = [p for p in (_parse_gpu_row(r) for r in rows) if p is not None]
    if not parsed:
        return (
            NvidiaDiagnostics(available=False, reason="вывод nvidia-smi не удалось разобрать"),
            None,
        )

    driver_version = next((d for d, _ in parsed if d), None)
    return (
        NvidiaDiagnostics(
            available=True,
            driver_version=driver_version,
            gpus=[gpu for _, gpu in parsed],
        ),
        NVIDIA_VENDOR_URL,
    )


# ---------------------------------------------------------------------------
# Хост: ОС, Python, аптайм, свободное место
# ---------------------------------------------------------------------------


def _human_bytes(size: int) -> str:
    for unit, factor in (("ГБ", 1024**3), ("МБ", 1024**2), ("КБ", 1024)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{size} Б"


def _existing_ancestor(path: str) -> str | None:
    """Ближайший существующий предок пути (включая сам путь).

    Нужен потому, что ``shutil.disk_usage`` на несуществующем пути бросает
    ``FileNotFoundError``, а на свежей установке каталога хранилища ещё нет —
    том при этом уже известен и место на нём измеримо.
    """
    candidate = Path(path).absolute()
    for parent in (candidate, *candidate.parents):
        try:
            if parent.exists():
                return str(parent)
        except OSError:
            return None
    return None


def _measure_disk(label: str, path: str) -> DiskDiagnostics:
    """Свободное место для одной точки хранения; сбой — статус, не падение."""
    target = str(Path(path).absolute())
    measured = _existing_ancestor(path)
    if measured is None:
        return DiskDiagnostics(
            label=label,
            path=target,
            available=False,
            reason="Путь не существует и не имеет существующего предка",
        )
    try:
        usage = shutil.disk_usage(measured)
    except OSError as exc:
        return DiskDiagnostics(
            label=label,
            path=target,
            measured_path=measured,
            available=False,
            reason=f"Свободное место не читается: {exc}",
        )
    return DiskDiagnostics(
        label=label,
        path=target,
        measured_path=None if measured == target else measured,
        available=True,
        free_bytes=usage.free,
        total_bytes=usage.total,
    )


def _sqlite_path(database_url: str) -> str | None:
    """Путь к файлу sqlite из URL; None для других СУБД и для базы в памяти."""
    if not database_url.startswith("sqlite"):
        return None
    _, _, rest = database_url.partition(":///")
    if not rest or rest.startswith(":memory:"):
        return None
    return rest


def _host_disks(settings: Settings) -> list[DiskDiagnostics]:
    """Точки хранения на этом хосте: документы, векторный индекс, база.

    Пункт пропускается, когда данные лежат вне хоста: каталог документов —
    при ``blob_store_backend=s3``, файл индекса — при внешнем Qdrant.
    Показать вместо них свободное место локального тома значило бы выдать
    чужое хранилище за своё.
    """
    disks: list[DiskDiagnostics] = []
    if settings.blob_store_backend != "s3":
        disks.append(_measure_disk("Хранилище документов", settings.blob_store_path))
    if settings.vector_store == "sqlite-vec":
        disks.append(_measure_disk("Векторный индекс", settings.vector_store_path))
    db_path = _sqlite_path(settings.database_url)
    if db_path is not None:
        disks.append(_measure_disk("База данных", db_path))
    return disks


def _uptime_seconds(started_at: datetime | None) -> int | None:
    if started_at is None:
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - started_at).total_seconds()))


async def _host_section(settings: Settings, started_at: datetime | None) -> HostDiagnostics:
    return HostDiagnostics(
        os_name=platform.system(),
        os_version=platform.release(),
        python_version=platform.python_version(),
        started_at=started_at,
        uptime_seconds=_uptime_seconds(started_at),
        # Обращение к тому — системный вызов, поэтому вне event loop.
        disks=await asyncio.to_thread(_host_disks, settings),
    )


# ---------------------------------------------------------------------------
# Внешние сервисы: накопленный результат зонда, своего запроса нет
# ---------------------------------------------------------------------------


def _available_model_count(provider: Provider) -> int:
    models = (provider.capabilities or {}).get("available_models")
    return len(models) if isinstance(models, list) else 0


def _probe_status(provider: Provider, interval_seconds: int) -> tuple[str, str | None]:
    """Статус провайдера по данным последнего зонда."""
    if not provider.enabled:
        return "disabled", "Провайдер отключён — плановый зонд его не проверяет"
    if provider.last_probe_at is None:
        return "never_probed", (
            "Зонд ещё не выполнялся: планировщик спит перед первым прогоном "
            f"(интервал {interval_seconds} с)"
        )
    if _available_model_count(provider) > 0:
        return "ok", None
    return "no_models", (
        "Последний зонд не сообщил ни одной доступной модели. Причина отказа "
        "в базе не сохраняется, поэтому отличить сбой провайдера от пустого "
        "списка моделей здесь нельзя — текст ошибки показывает кнопка "
        "«Проверить» в разделе «Провайдеры»."
    )


async def _embedder_provider_id(
    session: AsyncSession, workspace_id: str, settings: Settings
) -> str | None:
    """Провайдер, через который идут эмбеддинги, или None.

    Фильтр ``Model.enabled`` не применяется намеренно: отключённая модель
    должна быть видна как причина неработающих эмбеддингов, а не исчезать
    из раздела.
    """
    if settings.embeddings_backend != "provider" or not settings.embeddings_model_alias:
        return None
    row = await session.execute(
        select(Model.provider_id)
        .where(
            Model.workspace_id == workspace_id,
            Model.alias == settings.embeddings_model_alias,
        )
        .limit(1)
    )
    return row.scalar_one_or_none()


def _embedder_not_configured(settings: Settings) -> ExternalServiceDiagnostics:
    """Эмбеддинги настроены на провайдера, но свести их к нему не удалось."""
    alias = settings.embeddings_model_alias
    reason = (
        "ORQION_EMBEDDINGS_MODEL_ALIAS не задан при embeddings_backend=provider — "
        "эмбеддинги не работают"
        if not alias
        else f"Модель с алиасом «{alias}» не найдена в рабочей области — эмбеддинги не работают"
    )
    return ExternalServiceDiagnostics(
        id="embeddings",
        kind=None,
        role="embedder",
        status="not_configured",
        reason=reason,
    )


async def _services_section(
    session: AsyncSession, workspace_id: str, settings: Settings
) -> list[ExternalServiceDiagnostics]:
    result = await session.execute(
        select(Provider)
        .where(Provider.workspace_id == workspace_id)
        .options(selectinload(Provider.models))
    )
    providers = list(result.scalars().unique().all())
    embedder_id = await _embedder_provider_id(session, workspace_id, settings)

    entries: list[ExternalServiceDiagnostics] = []
    for provider in sorted(providers, key=lambda p: (p.kind, p.base_url)):
        status, reason = _probe_status(provider, settings.probe_interval_seconds)
        entries.append(
            ExternalServiceDiagnostics(
                id=provider.id,
                kind=provider.kind,
                role="embedder" if provider.id == embedder_id else "llm",
                base_url=provider.base_url,
                status=status,
                last_probe_at=provider.last_probe_at,
                reason=reason,
                model_count=len(provider.models or []),
                available_model_count=_available_model_count(provider),
            )
        )
    if settings.embeddings_backend == "provider" and embedder_id is None:
        entries.append(_embedder_not_configured(settings))
    return entries


# ---------------------------------------------------------------------------
# Локальные компоненты
# ---------------------------------------------------------------------------


def _module_installed(module_name: str) -> bool:
    """Установлен ли пакет — без импорта и без загрузки весов модели."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _embedding_component(settings: Settings, installed: bool) -> LocalComponentDiagnostics:
    name = "Эмбеддинги (локальный пакет)"
    if settings.embeddings_backend != "local":
        return LocalComponentDiagnostics(
            name=name,
            available=None,
            reason="Эмбеддинги идут через провайдера — локальный пакет не используется",
            detail=f"Алиас модели: {settings.embeddings_model_alias or 'не задан'}",
        )
    if not installed:
        return LocalComponentDiagnostics(
            name=name,
            available=False,
            reason=f"Пакет {EMBEDDING_PACKAGE} не установлен — локальные эмбеддинги не работают",
            detail="Установите orqion[full]",
        )
    return LocalComponentDiagnostics(
        name=name,
        available=True,
        detail=f"Модель {settings.embeddings_model}; веса загружаются при первом обращении",
    )


def _reranker_component(installed: bool) -> LocalComponentDiagnostics:
    name = "Реранкинг (локальный пакет)"
    if not installed:
        return LocalComponentDiagnostics(
            name=name,
            available=False,
            reason=f"Пакет {EMBEDDING_PACKAGE} не установлен — поиск работает без переранжирования",
            detail="Штатная деградация: выдача остаётся в порядке гибридного поиска",
        )
    return LocalComponentDiagnostics(
        name=name,
        available=True,
        detail="Пакет установлен; веса модели загружаются при первом поиске",
    )


def _file_component(name: str, path: str, *, absent_reason: str) -> LocalComponentDiagnostics:
    """Компонент, который определяется наличием файла или каталога."""
    target = Path(path)
    if not target.exists():
        return LocalComponentDiagnostics(
            name=name, available=None, reason=absent_reason, detail=str(target)
        )
    try:
        size = target.stat().st_size if target.is_file() else None
    except OSError as exc:
        return LocalComponentDiagnostics(
            name=name, available=False, reason=f"Не читается: {exc}", detail=str(target)
        )
    detail = f"{target} ({_human_bytes(size)})" if size is not None else str(target)
    return LocalComponentDiagnostics(name=name, available=True, detail=detail)


def _database_component(settings: Settings) -> LocalComponentDiagnostics:
    db_path = _sqlite_path(settings.database_url)
    if db_path is not None:
        return _file_component(
            "База данных", db_path, absent_reason="Файл ещё не создан — появится при первом запуске"
        )
    if settings.database_url.startswith("sqlite"):
        return LocalComponentDiagnostics(
            name="База данных",
            available=None,
            reason="База в памяти процесса — файла на диске нет",
            detail="sqlite (in-memory)",
        )
    # URL соединения может содержать пароль — наружу отдаётся только схема.
    scheme = settings.database_url.partition("://")[0]
    return LocalComponentDiagnostics(
        name="База данных",
        available=None,
        reason="СУБД работает вне этого хоста — локального файла нет",
        detail=scheme,
    )


def _blob_component(settings: Settings) -> LocalComponentDiagnostics:
    name = "Хранилище документов"
    if settings.blob_store_backend == "s3":
        return LocalComponentDiagnostics(
            name=name,
            available=None,
            reason="Документы хранятся в S3 — вне этого хоста",
            detail=settings.s3_bucket or None,
        )
    return _file_component(
        name,
        settings.blob_store_path,
        absent_reason="Каталог ещё не создан — появится при первой загрузке",
    )


def _vector_component(settings: Settings) -> LocalComponentDiagnostics:
    name = "Векторное хранилище"
    if settings.vector_store != "sqlite-vec":
        return LocalComponentDiagnostics(
            name=name,
            available=None,
            reason="Индекс хранится во внешнем Qdrant — вне этого хоста",
            detail=settings.qdrant_url or None,
        )
    return _file_component(
        name,
        settings.vector_store_path,
        absent_reason="Файл ещё не создан — появится при первой индексации",
    )


async def _components_section(settings: Settings) -> list[LocalComponentDiagnostics]:
    # Проверки, способные заблокировать поток (поиск пакета, stat, чтение
    # расширения), выполняются вне event loop.
    installed = await asyncio.to_thread(_module_installed, EMBEDDING_PACKAGE)
    uses_sqlite_vec = settings.vector_store == "sqlite-vec"
    vec_ok: bool | None
    vec_reason: str | None
    if uses_sqlite_vec:
        vec_ok, vec_reason = await asyncio.to_thread(sqlite_vec_status)
    else:
        # Расширение не нужно вовсе — сообщать «не установлено» было бы
        # диагнозом несуществующей проблемы.
        vec_ok, vec_reason = None, "Не используется: индекс хранится во внешнем Qdrant"

    vector_store, blob_store, database = await asyncio.gather(
        asyncio.to_thread(_vector_component, settings),
        asyncio.to_thread(_blob_component, settings),
        asyncio.to_thread(_database_component, settings),
    )
    return [
        _embedding_component(settings, installed),
        _reranker_component(installed),
        LocalComponentDiagnostics(
            name="sqlite-vec (векторный поиск)",
            available=vec_ok,
            reason=vec_reason,
            detail="Расширение грузится динамически при открытии хранилища"
            if uses_sqlite_vec
            else None,
        ),
        vector_store,
        blob_store,
        database,
    ]


async def collect_environment_diagnostics(
    *,
    settings: Settings,
    session: AsyncSession | None = None,
    workspace_id: str | None = None,
    started_at: datetime | None = None,
) -> EnvironmentDiagnosticsResponse:
    """Снимок окружения; каждое недоступное поле — null, не падение.

    ``session`` и ``workspace_id`` нужны только разделу внешних сервисов:
    без них список пуст, а хост, диск и GPU отдаются полностью.

    ``started_at`` приходит из ``app.state`` (фиксируется в lifespan):
    модуль импортируется до запуска приложения, поэтому время старта
    процесса он сам знать не может, а «время импорта модуля» было бы
    неверным значением.
    """
    raw = await _run_nvidia_smi_query()
    nvidia, vendor_url = _nvidia_section(raw)
    host = await _host_section(settings, started_at)
    components = await _components_section(settings)

    # Сессия и workspace_id передаются вместе: список сервисов — это строки
    # провайдера конкретной рабочей области. Отдельного перехвата ошибок БД
    # нет намеренно: до этой точки запрос уже прошёл аутентификацию и
    # проверку права, которые читают ту же базу, поэтому её недоступность
    # здесь невозможна.
    services: list[ExternalServiceDiagnostics] = []
    if session is not None and workspace_id is not None:
        services = await _services_section(session, workspace_id, settings)

    return EnvironmentDiagnosticsResponse(
        nvidia=nvidia,
        vendor_url=vendor_url,
        host=host,
        services=services,
        components=components,
    )
