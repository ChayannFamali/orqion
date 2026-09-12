"""T-444, T-511: диагностика окружения (read-only).

Приёмка T-444: отсутствие nvidia-smi не ломает страницу (graceful
«недоступно»); гейт view_diagnostics (по умолчанию только admin через "*");
раздел только читает — никаких действий.

Приёмка T-511: хост (ОС, Python, аптайм), свободное место томов хранения,
статус внешних сервисов по накопленному результату зонда (включая отдельное
состояние «ещё не проверялся») и локальные компоненты. Недоступность одного
пункта не валит остальные.

Вызовы подменяются заглушкой — реальный nvidia-smi не запускается, сетевых
запросов к провайдерам раздел не делает вовсе.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, User
from app.policy.presets import BUILTIN_ROLES
from fastapi import FastAPI

NVIDIA_SMI_CSV = (
    "551.86, NVIDIA GeForce RTX 4090, 1024, 24564, 45, 12\n"
    "551.86, NVIDIA RTX A6000, [N/A], 49140, 38, 0\n"
)


async def _login(api_client: httpx.AsyncClient, app_fixture: FastAPI, role: str) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role_obj = Role(
            workspace_id=workspace_id,
            name=role,
            is_builtin=True,
            policy=BUILTIN_ROLES[role].model_dump(),
        )
        session.add(role_obj)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email=f"diag-{role}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role_obj.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)


def _patch_query(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    async def _stub() -> Any:
        return result

    monkeypatch.setattr("app.diagnostics._run_nvidia_smi_query", _stub)


@pytest.mark.asyncio
async def test_environment_requires_auth(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_environment_forbidden_without_capability(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Роль без view_diagnostics — 404 (существование раздела не раскрывается)."""
    await _login(api_client, app_fixture, "developer")
    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_environment_admin_reads_gpu_metrics(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, NVIDIA_SMI_CSV)

    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 200
    body = response.json()

    nvidia = body["nvidia"]
    assert nvidia["available"] is True
    assert nvidia["driver_version"] == "551.86"
    assert len(nvidia["gpus"]) == 2

    first, second = nvidia["gpus"]
    assert first["name"] == "NVIDIA GeForce RTX 4090"
    assert first["memory_used_mib"] == 1024
    assert first["memory_total_mib"] == 24564
    assert first["temperature_c"] == 45
    assert first["utilization_percent"] == 12
    # [N/A] — честный null, не 0
    assert second["memory_used_mib"] is None
    assert second["memory_total_mib"] == 49140

    assert body["vendor_url"] == "https://www.nvidia.com/en-us/drivers/"


@pytest.mark.asyncio
async def test_environment_graceful_when_tool_missing(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет nvidia-smi — «недоступно», 200, без падения (приёмка задачи)."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)

    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 200
    body = response.json()
    assert body["nvidia"]["available"] is False
    assert body["nvidia"]["reason"]
    assert body["nvidia"]["gpus"] == []
    assert body["vendor_url"] is None


@pytest.mark.asyncio
async def test_environment_graceful_on_garbled_output(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неразбираемый вывод — «недоступно», не 500."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, "not a csv at all\n")

    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 200
    assert response.json()["nvidia"]["available"] is False


@pytest.mark.asyncio
async def test_environment_partial_row_fields_null(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нечисловые метрики — null по полю, строка остаётся в списке."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, "551.86, RTX 4090, err, 24564, 45, 12\n")

    response = await api_client.get("/api/diagnostics/environment")
    assert response.status_code == 200
    gpu = response.json()["nvidia"]["gpus"][0]
    assert gpu["memory_used_mib"] is None
    assert gpu["memory_total_mib"] == 24564


# ---------------------------------------------------------------------------
# T-511: хост, диск, сервисы, локальные компоненты
# ---------------------------------------------------------------------------

ENVIRONMENT_PATH = "/api/diagnostics/environment"


async def _seed_provider(
    app_fixture: FastAPI,
    *,
    kind: str = "openai",
    base_url: str = "http://stub:1234/v1",
    enabled: bool = True,
    last_probe_at: datetime | None = None,
    capabilities: dict[str, Any] | None = None,
    model_alias: str | None = None,
) -> str:
    """Провайдер (опционально с моделью) в рабочей области фикстуры."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind=kind,
            base_url=base_url,
            api_key_enc=encrypt_api_key("sk-test", app_fixture.state.secret_key),
            enabled=enabled,
            capabilities=capabilities or {},
            last_probe_at=last_probe_at,
        )
        session.add(provider)
        await session.flush()
        if model_alias is not None:
            session.add(
                Model(
                    workspace_id=workspace_id,
                    provider_id=provider.id,
                    alias=model_alias,
                    upstream_name="embed-model",
                    locality="local",
                    max_input_tokens=8000,
                    enabled=True,
                )
            )
        await session.commit()
        return provider.id


async def _environment(api_client: httpx.AsyncClient) -> dict[str, Any]:
    response = await api_client.get(ENVIRONMENT_PATH)
    assert response.status_code == 200, response.text[:300]
    body: dict[str, Any] = response.json()
    return body


@pytest.mark.asyncio
async def test_environment_returns_host_section(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)

    host = (await _environment(api_client))["host"]

    assert host["os_name"]
    assert host["python_version"].count(".") == 2


@pytest.mark.asyncio
async def test_environment_lists_disks_for_local_paths(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Локальные точки хранения; место измеряется даже до их создания.

    Файл базы — локальная точка хранения только при SQLite: на PostgreSQL
    база лежит вне этого хоста, и показать вместо неё свободное место
    локального тома значило бы выдать чужое хранилище за своё (тот же
    принцип, что для s3 и внешнего Qdrant). Поэтому состав выводится из
    диалекта, а не перечисляется жёстко: перечисление верно лишь в одной
    ноге CI, а в postgres-ноге «Базы данных» в списке нет.

    Критерий взят независимый от кода приложения (префикс URL, а не
    ``_sqlite_path``), чтобы тест не проходил вместе со сломанным
    ``_sqlite_path``.
    """
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)

    disks = (await _environment(api_client))["host"]["disks"]
    db_is_local_file = app_fixture.state.settings.database_url.startswith("sqlite")

    expected_labels = ["Хранилище документов", "Векторный индекс"]
    if db_is_local_file:
        expected_labels.append("База данных")
    assert [d["label"] for d in disks] == expected_labels
    assert any(d["label"] == "База данных" for d in disks) is db_is_local_file
    assert all(d["available"] is True for d in disks)
    assert all(d["free_bytes"] > 0 and d["total_bytes"] >= d["free_bytes"] for d in disks)
    # Если самого пути ещё нет, измерение идёт по существующему предку и это
    # отражено явно — чужой каталог не выдаётся за своё хранилище.
    for disk in disks:
        if not Path(disk["path"]).exists():
            assert disk["measured_path"] is not None, disk["label"]


@pytest.mark.asyncio
async def test_environment_omits_db_disk_when_url_is_not_sqlite(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ветка postgres исполняется и в sqlite-ноге: файла базы в томах нет.

    Подменяется только ``settings.database_url``, который диагностика читает
    при запросе; само соединение остаётся sqlite-файлом фикстуры. Этого
    достаточно, потому что состав томов выводится из конфигурации, а не из
    живого соединения. Без этого теста ветка «СУБД вне хоста» исполнялась
    только в postgres-ноге CI — именно так жёсткое ожидание трёх томов
    прошло локальную sqlite-ногу и упало в CI.

    Компонент «База данных» при этом остаётся: он описывает схему соединения,
    а не локальный том, и пароль из URL наружу не уходит.
    """
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    monkeypatch.setattr(
        app_fixture.state.settings,
        "database_url",
        "postgresql+asyncpg://orqion:secret@db.internal:5432/orqion",
    )

    body = await _environment(api_client)

    assert [disk["label"] for disk in body["host"]["disks"]] == [
        "Хранилище документов",
        "Векторный индекс",
    ]
    db_component = next(c for c in body["components"] if c["name"] == "База данных")
    assert db_component["available"] is None
    assert db_component["detail"] == "postgresql+asyncpg"
    assert "secret" not in str(db_component)


@pytest.mark.asyncio
async def test_environment_lists_local_components(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)

    names = [c["name"] for c in (await _environment(api_client))["components"]]

    assert names == [
        "Эмбеддинги (локальный пакет)",
        "Реранкинг (локальный пакет)",
        "sqlite-vec (векторный поиск)",
        "Векторное хранилище",
        "Хранилище документов",
        "База данных",
    ]


@pytest.mark.asyncio
async def test_environment_absent_files_are_unknown_not_broken(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Свежая установка: файлов хранилищ ещё нет — available=null, не false."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)

    components = {c["name"]: c for c in (await _environment(api_client))["components"]}

    vector = components["Векторное хранилище"]
    assert vector["available"] is None
    assert vector["reason"] is not None and "ещё не создан" in vector["reason"]


@pytest.mark.asyncio
async def test_environment_service_never_probed_is_not_unavailable(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Зонд спит перед первым прогоном — отдельное состояние, не «недоступен»."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    provider_id = await _seed_provider(app_fixture)

    services = (await _environment(api_client))["services"]
    entry = next(s for s in services if s["id"] == provider_id)

    assert entry["status"] == "never_probed"
    assert entry["last_probe_at"] is None
    assert entry["reason"] is not None and "спит" in entry["reason"]
    assert entry["role"] == "llm"
    assert entry["kind"] == "openai"
    assert entry["base_url"] == "http://stub:1234/v1"


@pytest.mark.asyncio
async def test_environment_service_ok_after_probe(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    probed_at = datetime.now(UTC)
    provider_id = await _seed_provider(
        app_fixture,
        last_probe_at=probed_at,
        capabilities={"available_models": ["qwen3-8b", "bge-m3"]},
    )

    entry = next(s for s in (await _environment(api_client))["services"] if s["id"] == provider_id)

    assert entry["status"] == "ok"
    assert entry["available_model_count"] == 2
    assert entry["last_probe_at"] is not None
    assert entry["reason"] is None


@pytest.mark.asyncio
async def test_environment_service_no_models_explains_limitation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Зонд прошёл, моделей ноль: причина отказа в БД не хранится — это сказано."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    provider_id = await _seed_provider(
        app_fixture, last_probe_at=datetime.now(UTC), capabilities={"available_models": []}
    )

    entry = next(s for s in (await _environment(api_client))["services"] if s["id"] == provider_id)

    assert entry["status"] == "no_models"
    assert entry["reason"] is not None and "«Проверить»" in entry["reason"]


@pytest.mark.asyncio
async def test_environment_service_disabled_is_not_a_failure(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    provider_id = await _seed_provider(app_fixture, enabled=False)

    entry = next(s for s in (await _environment(api_client))["services"] if s["id"] == provider_id)

    assert entry["status"] == "disabled"
    assert entry["reason"] is not None and "отключён" in entry["reason"]


@pytest.mark.asyncio
async def test_environment_counts_registered_models(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сколько моделей зарегистрировано в orqion — отдельно от ответа зонда."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    provider_id = await _seed_provider(app_fixture, model_alias="local/embed")

    entry = next(s for s in (await _environment(api_client))["services"] if s["id"] == provider_id)

    assert entry["model_count"] == 1
    assert entry["available_model_count"] == 0


@pytest.mark.asyncio
async def test_environment_marks_embedder_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Провайдер, через который идут эмбеддинги, помечен ролью."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    provider_id = await _seed_provider(
        app_fixture,
        base_url="http://embed-stub:1234/v1",
        model_alias="local/embed-model",
        last_probe_at=datetime.now(UTC),
        capabilities={"available_models": ["embed-model"]},
    )
    # Только два поля эмбеддингов: остальная конфигурация (включая ключ и БД)
    # остаётся прежней, иначе проверка сессии перестанет работать.
    app_fixture.state.settings = app_fixture.state.settings.model_copy(
        update={
            "embeddings_backend": "provider",
            "embeddings_model_alias": "local/embed-model",
        }
    )

    services = (await _environment(api_client))["services"]
    entry = next(s for s in services if s["id"] == provider_id)

    assert entry["role"] == "embedder"
    assert entry["status"] == "ok"


@pytest.mark.asyncio
async def test_environment_reports_unresolvable_embedder(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Эмбеддинги настроены на провайдера, но модели нет — это видно как причина."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    app_fixture.state.settings = app_fixture.state.settings.model_copy(
        update={
            "embeddings_backend": "provider",
            "embeddings_model_alias": "local/несуществующая",
        }
    )

    services = (await _environment(api_client))["services"]
    entry = next(s for s in services if s["role"] == "embedder")

    assert entry["status"] == "not_configured"
    assert entry["id"] == "embeddings"
    assert entry["reason"] is not None and "не найдена" in entry["reason"]


@pytest.mark.asyncio
async def test_environment_uptime_absent_without_lifespan(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Приложение собрано без lifespan — аптайм честно null, а не ноль."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)

    host = (await _environment(api_client))["host"]

    assert host["started_at"] is None
    assert host["uptime_seconds"] is None


@pytest.mark.asyncio
async def test_environment_reports_uptime_from_app_state(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """started_at берётся из app.state, куда его кладёт lifespan."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    app_fixture.state.started_at = datetime.now(UTC)

    host = (await _environment(api_client))["host"]

    assert host["started_at"] is not None
    assert 0 <= host["uptime_seconds"] <= 60


@pytest.mark.asyncio
async def test_environment_sections_survive_missing_gpu(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Приёмка: недоступность одного параметра не валит и не блокирует остальные."""
    await _login(api_client, app_fixture, "admin")
    _patch_query(monkeypatch, None)
    await _seed_provider(app_fixture)

    body = await _environment(api_client)

    assert body["nvidia"]["available"] is False
    assert body["host"]["disks"]
    assert body["services"]
    assert body["components"]
