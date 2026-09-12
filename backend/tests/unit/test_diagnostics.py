"""T-444, T-511: юнит-тесты сбора окружения (без subprocess и сети)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from app.config import Settings
from app.db.models import Provider
from app.diagnostics import (
    _available_model_count,
    _blob_component,
    _database_component,
    _embedding_component,
    _existing_ancestor,
    _file_component,
    _host_disks,
    _measure_disk,
    _module_installed,
    _parse_gpu_row,
    _probe_status,
    _reranker_component,
    _run_nvidia_smi_query,
    _sqlite_path,
    _uptime_seconds,
    _vector_component,
    collect_environment_diagnostics,
)
from app.rag.vector_store import sqlite_vec_status


def _provider(
    *,
    enabled: bool = True,
    last_probe_at: datetime | None = None,
    capabilities: dict[str, Any] | None = None,
) -> Provider:
    """Настоящая строка Provider без сессии.

    Сборщик читает только ``enabled``, ``last_probe_at`` и ``capabilities``,
    поэтому ленивые связи (в частности ``models``) здесь не загружаются.
    """
    return Provider(
        workspace_id="ws-diag",
        kind="openai",
        base_url="http://stub:1234/v1",
        enabled=enabled,
        capabilities=capabilities,
        last_probe_at=last_probe_at,
    )


def _settings(**overrides: Any) -> Settings:
    """Настройки с явными значениями: проверка не зависит от окружения машины."""
    base: dict[str, Any] = {
        "blob_store_path": "blobs",
        "vector_store_path": "vec.db",
        "database_url": "sqlite:///./orqion.db",
        "blob_store_backend": "local",
        "vector_store": "sqlite-vec",
        "embeddings_backend": "local",
        "embeddings_model_alias": "text-embedding-bge-m3",
        "profile": "minimal",
    }
    base.update(overrides)
    return Settings(**base)


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


# ---------------------------------------------------------------------------
# Аптайм
# ---------------------------------------------------------------------------


def test_uptime_none_without_started_at() -> None:
    """Приложение собрано без lifespan — честное null, а не ноль."""
    assert _uptime_seconds(None) is None


def test_uptime_counts_from_start() -> None:
    started = datetime.now(UTC) - timedelta(seconds=90)
    uptime = _uptime_seconds(started)
    assert uptime is not None
    assert 85 <= uptime <= 120


def test_uptime_never_negative_on_clock_skew() -> None:
    """Старт в будущем (перевод часов) не даёт отрицательного аптайма."""
    assert _uptime_seconds(datetime.now(UTC) + timedelta(hours=1)) == 0


def test_uptime_accepts_naive_datetime() -> None:
    """Наивный timestamp трактуется как UTC, а не как локальное время."""
    started = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=30)
    uptime = _uptime_seconds(started)
    assert uptime is not None
    assert 25 <= uptime <= 60


# ---------------------------------------------------------------------------
# Диск
# ---------------------------------------------------------------------------


def test_sqlite_path_extracts_file() -> None:
    assert _sqlite_path("sqlite:///./orqion.db") == "./orqion.db"
    assert _sqlite_path("sqlite+aiosqlite:///C:/data/orqion.db") == "C:/data/orqion.db"


def test_sqlite_path_none_for_other_backends_and_memory() -> None:
    assert _sqlite_path("postgresql+asyncpg://u:p@host/db") is None
    assert _sqlite_path("sqlite:///:memory:") is None
    assert _sqlite_path("sqlite://") is None


def test_existing_ancestor_falls_back_to_parent(tmp_path: Path) -> None:
    """Свежая установка: каталога ещё нет, но том измерим по предку."""
    missing = tmp_path / "not-created-yet" / "blobs"
    assert _existing_ancestor(str(missing)) == str(tmp_path)


def test_measure_disk_uses_nearest_existing_ancestor(tmp_path: Path) -> None:
    missing = tmp_path / "not-created-yet"
    disk = _measure_disk("Хранилище документов", str(missing))

    assert disk.available is True
    assert disk.measured_path == str(tmp_path)
    assert disk.free_bytes is not None and disk.free_bytes > 0
    assert disk.total_bytes is not None and disk.total_bytes >= disk.free_bytes


def test_measure_disk_reports_failure_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой системного вызова — статус с причиной, не исключение."""

    def _boom(_path: str) -> Any:
        raise OSError("нет доступа")

    monkeypatch.setattr("app.diagnostics.shutil.disk_usage", _boom)
    disk = _measure_disk("Векторный индекс", "vec.db")

    assert disk.available is False
    assert disk.reason is not None and "нет доступа" in disk.reason
    assert disk.free_bytes is None


def test_host_disks_lists_local_paths() -> None:
    settings = _settings()
    labels = [d.label for d in _host_disks(settings)]
    assert labels == ["Хранилище документов", "Векторный индекс", "База данных"]


def test_host_disks_skips_s3_blob_store() -> None:
    """Документы в S3 — локальный том к ним отношения не имеет."""
    settings = _settings(blob_store_backend="s3")
    labels = [d.label for d in _host_disks(settings)]
    assert "Хранилище документов" not in labels


def test_host_disks_skips_external_vector_store() -> None:
    """Qdrant внешний — файла индекса на хосте нет."""
    settings = _settings(vector_store="qdrant")
    labels = [d.label for d in _host_disks(settings)]
    assert "Векторный индекс" not in labels


def test_host_disks_skips_db_entry_for_postgres() -> None:
    settings = _settings(
        database_url="postgresql+asyncpg://u:p@localhost/orqion",
        profile="standard",
    )
    labels = [d.label for d in _host_disks(settings)]
    assert "База данных" not in labels


# ---------------------------------------------------------------------------
# Статус провайдера по накопленному зонду
# ---------------------------------------------------------------------------


def test_probe_status_ok_when_models_reported() -> None:
    provider = _provider(
        last_probe_at=datetime.now(UTC),
        capabilities={"available_models": ["qwen3-8b", "bge-m3"]},
    )
    status, reason = _probe_status(provider, interval_seconds=900)
    assert status == "ok"
    assert reason is None


def test_probe_status_never_probed_is_not_unavailable() -> None:
    """Планировщик спит перед первым прогоном — это отдельное состояние."""
    provider = _provider(last_probe_at=None)
    status, reason = _probe_status(provider, interval_seconds=900)
    assert status == "never_probed"
    assert reason is not None and "900" in reason


def test_probe_status_no_models_when_probe_reported_empty() -> None:
    provider = _provider(last_probe_at=datetime.now(UTC), capabilities={})
    status, reason = _probe_status(provider, interval_seconds=900)
    assert status == "no_models"
    # Причина отказа в БД не сохраняется — это сказано пользователю явно.
    assert reason is not None and "«Проверить»" in reason


def test_probe_status_no_models_on_garbage_capabilities() -> None:
    provider = _provider(
        last_probe_at=datetime.now(UTC),
        capabilities={"available_models": "не список"},
    )
    status, _ = _probe_status(provider, interval_seconds=900)
    assert status == "no_models"


def test_probe_status_disabled() -> None:
    provider = _provider(enabled=False, last_probe_at=datetime.now(UTC))
    status, reason = _probe_status(provider, interval_seconds=900)
    assert status == "disabled"
    assert reason is not None


def test_available_model_count_handles_missing_capabilities() -> None:
    assert _available_model_count(_provider(capabilities=None)) == 0
    assert _available_model_count(_provider(capabilities={})) == 0
    assert _available_model_count(_provider(capabilities={"available_models": ["a", "b"]})) == 2


# ---------------------------------------------------------------------------
# Локальные компоненты
# ---------------------------------------------------------------------------


def test_embedding_component_not_used_when_backend_is_provider() -> None:
    settings = _settings(embeddings_backend="provider")
    component = _embedding_component(settings, installed=True)
    # Не «недоступно» и не «доступно»: пакет в этой конфигурации не нужен.
    assert component.available is None
    assert component.reason is not None and "провайдера" in component.reason


def test_embedding_component_reports_missing_package() -> None:
    component = _embedding_component(_settings(), installed=False)
    assert component.available is False
    assert component.reason is not None and "не установлен" in component.reason
    assert component.detail == "Установите orqion[full]"


def test_embedding_component_available() -> None:
    component = _embedding_component(_settings(), installed=True)
    assert component.available is True


def test_reranker_component_degrades_without_package() -> None:
    """Отсутствие пакета — штатная деградация поиска, не отказ системы."""
    component = _reranker_component(installed=False)
    assert component.available is False
    assert component.reason is not None and "без переранжирования" in component.reason


def test_reranker_component_available() -> None:
    assert _reranker_component(installed=True).available is True


def test_file_component_absent_is_unknown_not_broken(tmp_path: Path) -> None:
    """Свежая установка: файла ещё нет — это не отказ."""
    component = _file_component(
        "Векторное хранилище",
        str(tmp_path / "vec.db"),
        absent_reason="Файл ещё не создан",
    )
    assert component.available is None
    assert component.reason == "Файл ещё не создан"


def test_file_component_reports_size_for_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "vec.db"
    target.write_bytes(b"x" * 1024)
    component = _file_component("Векторное хранилище", str(target), absent_reason="нет")
    assert component.available is True
    assert component.detail is not None and "1.0 КБ" in component.detail


def test_file_component_for_directory_has_no_size(tmp_path: Path) -> None:
    component = _file_component("Хранилище документов", str(tmp_path), absent_reason="нет")
    assert component.available is True
    assert component.detail == str(tmp_path)


def test_database_component_reports_sqlite_file(tmp_path: Path) -> None:
    target = tmp_path / "orqion.db"
    target.write_bytes(b"sqlite")
    settings = _settings(database_url=f"sqlite:///{target}")
    component = _database_component(settings)
    assert component.available is True


def test_database_component_reports_external_backend_without_leaking_url() -> None:
    """В detail уходит только схема: URL соединения может содержать пароль."""
    settings = _settings(
        database_url="postgresql+asyncpg://user:secret@db.internal/orqion",
        profile="standard",
    )
    component = _database_component(settings)
    assert component.available is None
    assert component.reason is not None and "вне этого хоста" in component.reason
    assert component.detail == "postgresql+asyncpg"
    # Пароль из URL соединения не должен попасть ни в одно поле ответа.
    assert "secret" not in f"{component.reason} {component.detail}"


def test_database_component_reports_in_memory() -> None:
    component = _database_component(_settings(database_url="sqlite:///:memory:"))
    assert component.available is None
    assert component.reason is not None and "в памяти" in component.reason


# ---------------------------------------------------------------------------
# Хранилища вне хоста: s3 и внешний Qdrant
# ---------------------------------------------------------------------------


def test_blob_component_reports_s3_as_off_host(tmp_path: Path) -> None:
    settings = _settings(
        blob_store_backend="s3", s3_bucket="orqion-docs", blob_store_path=str(tmp_path)
    )
    component = _blob_component(settings)
    assert component.available is None
    assert component.reason is not None and "S3" in component.reason
    assert component.detail == "orqion-docs"


def test_blob_component_uses_local_path(tmp_path: Path) -> None:
    settings = _settings(blob_store_backend="local", blob_store_path=str(tmp_path))
    component = _blob_component(settings)
    assert component.available is True
    assert component.detail == str(tmp_path)


def test_vector_component_reports_qdrant_as_off_host(tmp_path: Path) -> None:
    settings = _settings(
        vector_store="qdrant", qdrant_url="http://qdrant:6333", vector_store_path=str(tmp_path)
    )
    component = _vector_component(settings)
    assert component.available is None
    assert component.reason is not None and "Qdrant" in component.reason
    assert component.detail == "http://qdrant:6333"


def test_vector_component_uses_local_file(tmp_path: Path) -> None:
    target = tmp_path / "vec.db"
    target.write_bytes(b"v")
    settings = _settings(vector_store="sqlite-vec", vector_store_path=str(target))
    component = _vector_component(settings)
    assert component.available is True


# ---------------------------------------------------------------------------
# Проверка пакетов без импорта
# ---------------------------------------------------------------------------


def test_module_installed_true_for_stdlib() -> None:
    assert _module_installed("json") is True


def test_module_installed_false_for_missing_package() -> None:
    assert _module_installed("orqion_definitely_not_installed_xyz") is False


def test_module_installed_swallows_broken_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повреждённый пакет не роняет раздел: диагностика отдаёт «не установлен»."""

    def _boom(_name: str) -> Any:
        raise ImportError("метаданные повреждены")

    monkeypatch.setattr("app.diagnostics.importlib.util.find_spec", _boom)
    assert _module_installed("FlagEmbedding") is False


def test_sqlite_vec_status_shape_is_honest() -> None:
    """Возвращает (bool, причина): причина есть ровно когда недоступно.

    Сам результат зависит от машины (extras [full]), поэтому проверяется
    инвариант, а не конкретное значение.
    """
    ok, reason = sqlite_vec_status()
    assert isinstance(ok, bool)
    if ok:
        assert reason is None
    else:
        assert isinstance(reason, str) and reason


# ---------------------------------------------------------------------------
# Сборщик целиком
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_without_session_returns_empty_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без сессии раздел сервисов пуст, остальные группы собираются.

    Это же поведение даёт CLI и сборка приложения без lifespan: раздел
    не падает, а честно отдаёт то, что может измерить.
    """

    async def _no_tool() -> None:
        return None

    monkeypatch.setattr("app.diagnostics._run_nvidia_smi_query", _no_tool)

    response = await collect_environment_diagnostics(settings=_settings())

    assert response.services == []
    assert response.nvidia.available is False
    assert response.vendor_url is None
    assert response.host.python_version
    assert response.host.uptime_seconds is None
    assert response.host.started_at is None
    assert {c.name for c in response.components} >= {
        "Реранкинг (локальный пакет)",
        "sqlite-vec (векторный поиск)",
    }


@pytest.mark.asyncio
async def test_collect_reports_uptime_from_started_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """started_at приходит из app.state — модуль сам его знать не может."""

    async def _no_tool() -> None:
        return None

    monkeypatch.setattr("app.diagnostics._run_nvidia_smi_query", _no_tool)
    started = datetime.now(UTC) - timedelta(seconds=45)

    response = await collect_environment_diagnostics(settings=_settings(), started_at=started)

    assert response.host.started_at == started
    assert response.host.uptime_seconds is not None
    assert 40 <= response.host.uptime_seconds <= 75


@pytest.mark.asyncio
async def test_collect_gpu_section_does_not_break_other_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GPU недоступен — хост, диск и компоненты всё равно собираются."""

    async def _no_tool() -> None:
        return None

    monkeypatch.setattr("app.diagnostics._run_nvidia_smi_query", _no_tool)

    response = await collect_environment_diagnostics(settings=_settings())

    assert response.nvidia.available is False
    assert len(response.host.disks) == 3
    assert all(d.available is True for d in response.host.disks)


@pytest.mark.asyncio
async def test_sqlite_vec_component_skipped_for_qdrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """С внешним индексом расширение не нужно — сообщать отказ было бы ложным диагнозом."""

    async def _no_tool() -> None:
        return None

    monkeypatch.setattr("app.diagnostics._run_nvidia_smi_query", _no_tool)

    response = await collect_environment_diagnostics(
        settings=_settings(vector_store="qdrant", qdrant_url="http://qdrant:6333")
    )

    vec = next(c for c in response.components if c.name.startswith("sqlite-vec"))
    assert vec.available is None
    assert vec.reason is not None and "Qdrant" in vec.reason
    labels = [d.label for d in response.host.disks]
    assert "Векторный индекс" not in labels
