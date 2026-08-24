"""T-444: юнит-тесты best-effort чтения окружения (без subprocess)."""

from __future__ import annotations

import pytest
from app.diagnostics import _parse_gpu_row, _run_nvidia_smi_query


@pytest.mark.asyncio
async def test_run_query_returns_none_when_tool_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет исполняемого файла — честный None, не исключение."""
    monkeypatch.setattr("app.diagnostics.shutil.which", lambda _name: None)
    assert await _run_nvidia_smi_query() is None


def test_parse_gpu_row_full() -> None:
    parsed = _parse_gpu_row("551.86, NVIDIA GeForce RTX 4090, 1024, 24564, 45, 12")
    assert parsed is not None
    driver, gpu = parsed
    assert driver == "551.86"
    assert gpu.name == "NVIDIA GeForce RTX 4090"
    assert gpu.memory_used_mib == 1024
    assert gpu.temperature_c == 45


def test_parse_gpu_row_na_marker_becomes_null() -> None:
    parsed = _parse_gpu_row("551.86, RTX A6000, [N/A], 49140, [N/A], 0")
    assert parsed is not None
    _, gpu = parsed
    assert gpu.memory_used_mib is None
    assert gpu.temperature_c is None
    assert gpu.utilization_percent == 0


def test_parse_gpu_row_rejects_wrong_column_count() -> None:
    assert _parse_gpu_row("551.86, RTX 4090, 1024") is None


def test_parse_gpu_row_non_numeric_metrics_null() -> None:
    parsed = _parse_gpu_row("551.86, RTX 4090, err, 24564, 45, 12")
    assert parsed is not None
    _, gpu = parsed
    assert gpu.memory_used_mib is None
    assert gpu.memory_total_mib == 24564
