"""T-444: диагностика окружения хоста — только чтение, best-effort.

Читает метрики GPU через nvidia-smi (shutil.which + subprocess; pynvml
не используется — покрытие то же, а новая зависимость потребовала бы
отдельного согласования). Инструмент не найден или вызов не удался —
честный статус «недоступно», без падения и без предположений.

Read-only намеренно (arch.md §14.3): orqion не управляет драйверами и
backend'ом инференса — никаких действий по установке/обновлению здесь
нет и не будет.

Работает только для локального хоста (профиль minimal): удалённые
провайдеры не опрашиваются.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

from app.api.schemas.diagnostics import (
    EnvironmentDiagnosticsResponse,
    GpuInfo,
    NvidiaDiagnostics,
)

logger = logging.getLogger("orqion.diagnostics")

NVIDIA_SMI_TIMEOUT_SECONDS = 5.0
NVIDIA_VENDOR_URL = "https://www.nvidia.com/en-us/drivers/"

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


async def collect_environment_diagnostics() -> EnvironmentDiagnosticsResponse:
    """Снимок окружения; каждое недоступное поле — null, не падение."""
    raw = await _run_nvidia_smi_query()
    if raw is None:
        return EnvironmentDiagnosticsResponse(
            nvidia=NvidiaDiagnostics(
                available=False,
                reason="nvidia-smi не найден или недоступен",
            ),
        )

    rows = [line.strip() for line in raw.splitlines() if line.strip()]
    parsed = [p for p in (_parse_gpu_row(r) for r in rows) if p is not None]
    if not parsed:
        return EnvironmentDiagnosticsResponse(
            nvidia=NvidiaDiagnostics(
                available=False,
                reason="вывод nvidia-smi не удалось разобрать",
            ),
        )

    driver_version = next((d for d, _ in parsed if d), None)
    return EnvironmentDiagnosticsResponse(
        nvidia=NvidiaDiagnostics(
            available=True,
            driver_version=driver_version,
            gpus=[gpu for _, gpu in parsed],
        ),
        vendor_url=NVIDIA_VENDOR_URL,
    )
