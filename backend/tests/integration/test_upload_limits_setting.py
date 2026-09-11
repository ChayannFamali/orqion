"""Лимиты загрузки как настройки рабочей области.

Проверяется поведение, а не наличие записей: смена значения через API меняет
реальный исход загрузки без перезапуска приложения, а оба пути попадания
файла в корпус — загрузка через интерфейс и импорт git-репозитория — читают
одно и то же действующее значение.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.models import Role, User
from app.policy.presets import BUILTIN_ROLES
from app.rag.git_ingest import GitIngestResult
from app.settings.registry import ALLOWED_UPLOAD_EXTENSIONS_KEY, MAX_UPLOAD_SIZE_KEY
from app.settings.uploads import BYTES_PER_MB
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.fixtures.database import EnvFreeSettings

SETTINGS_PATH = "/api/workspace/settings"
LOGIN_PATH = "/api/auth/login"

#: Env-дефолты проверок: маршрут создаёт ``Settings()`` на каждый запрос,
#: поэтому переменные окружения задают исходную точку детерминированно.
ENV_SIZE_MB = 5
ENV_EXTENSIONS = ".md,.txt"


@pytest.fixture(autouse=True)
def _env_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORQION_MAX_UPLOAD_SIZE_MB", str(ENV_SIZE_MB))
    monkeypatch.setenv("ORQION_ALLOWED_UPLOAD_EXTENSIONS", ENV_EXTENSIONS)


async def _seed_user(app_fixture: FastAPI, *, role_name: str, email: str) -> tuple[str, str]:
    """Пользователь в рабочей области приложения; возвращает (email, пароль)."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    password = f"limits-{role_name}-pass-123"
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name=f"limits-{role_name}-{email}",
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
        )
        session.add(role)
        await session.flush()
        session.add(
            User(
                workspace_id=workspace_id,
                email=email,
                password_hash=hash_password(password),
                role_id=role.id,
                is_active=True,
            )
        )
        await session.commit()
    return email, password


async def _login_admin(api_client: httpx.AsyncClient, app_fixture: FastAPI, email: str) -> None:
    """Вход под администратором: у роли wildcard-право на запись настроек."""
    creds = await _seed_user(app_fixture, role_name="admin", email=email)
    api_client.cookies.clear()
    response = await api_client.post(LOGIN_PATH, json={"email": creds[0], "password": creds[1]})
    assert response.status_code == 200, response.text[:300]


async def _patch(api_client: httpx.AsyncClient, key: str, value: object) -> httpx.Response:
    return await api_client.patch(SETTINGS_PATH, json={"key": key, "value": value})


async def _create_corpus(app_fixture: FastAPI, name: str) -> str:
    """Корпус напрямую в БД: право на создание корпуса к проверке не относится."""
    from app.db.models import Corpus

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(name=name, workspace_id=workspace_id)
        session.add(corpus)
        await session.flush()
        corpus_id = corpus.id
        await session.commit()
    return corpus_id


async def _upload(
    api_client: httpx.AsyncClient,
    corpus_id: str,
    *,
    filename: str,
    size_bytes: int,
) -> httpx.Response:
    """Загрузка файла заданного размера; содержимое уникально по имени."""
    marker = filename.encode()
    content = marker + b"\n" + b"x" * max(size_bytes - len(marker) - 1, 0)
    return await api_client.post(
        f"/api/corpora/{corpus_id}/documents",
        files={"file": (filename, content, "text/plain")},
    )


def _entry(entries: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return next(item for item in entries if item["key"] == key)


# ---------------------------------------------------------------------------
# Загрузка через интерфейс
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_without_settings_uses_env_defaults(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Без записей в БД действуют прежние env-значения."""
    await _login_admin(api_client, app_fixture, "limits-admin-env@orqion.local")
    corpus_id = await _create_corpus(app_fixture, "limits-env")

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    size = _entry(entries, MAX_UPLOAD_SIZE_KEY)
    extensions = _entry(entries, ALLOWED_UPLOAD_EXTENSIONS_KEY)
    assert (size["value"], size["source"]) == (ENV_SIZE_MB, "default")
    assert (extensions["value"], extensions["source"]) == (ENV_EXTENSIONS, "default")
    assert size["category"] == extensions["category"] == "Файлы и хранение"
    assert (size["type"], size["min"], size["max"]) == ("integer", 1.0, 1024.0)
    assert extensions["type"] == "string"

    assert (
        await _upload(api_client, corpus_id, filename="ok.md", size_bytes=2048)
    ).status_code == (201)

    rejected = await _upload(api_client, corpus_id, filename="malware.exe", size_bytes=1024)
    assert rejected.status_code == 415
    assert ".exe" not in rejected.json()["constraint"]["allowed_extensions"]


@pytest.mark.asyncio
async def test_size_setting_changes_real_upload_without_restart(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Приёмка задачи: смена предела меняет исход загрузки на том же процессе."""
    await _login_admin(api_client, app_fixture, "limits-admin-size@orqion.local")
    corpus_id = await _create_corpus(app_fixture, "limits-size")
    two_mb = 2 * BYTES_PER_MB

    before = await _upload(api_client, corpus_id, filename="before.md", size_bytes=two_mb)
    assert before.status_code == 201, before.text[:300]

    response = await _patch(api_client, MAX_UPLOAD_SIZE_KEY, 1)
    assert response.status_code == 200, response.text[:300]
    assert response.json()["source"] == "db"

    after = await _upload(api_client, corpus_id, filename="after.md", size_bytes=two_mb)
    assert after.status_code == 413, after.text[:300]
    assert after.json()["error"] == "file_too_large"
    assert after.json()["constraint"]["max_size_bytes"] == BYTES_PER_MB

    small = await _upload(api_client, corpus_id, filename="small.md", size_bytes=4096)
    assert small.status_code == 201, small.text[:300]


@pytest.mark.asyncio
async def test_raised_limit_allows_previously_rejected_file(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Предел работает в обе стороны: увеличение разрешает прежний отказ."""
    await _login_admin(api_client, app_fixture, "limits-admin-raise@orqion.local")
    corpus_id = await _create_corpus(app_fixture, "limits-raise")
    six_mb = 6 * BYTES_PER_MB

    rejected = await _upload(api_client, corpus_id, filename="big.md", size_bytes=six_mb)
    assert rejected.status_code == 413

    assert (await _patch(api_client, MAX_UPLOAD_SIZE_KEY, 10)).status_code == 200

    accepted = await _upload(api_client, corpus_id, filename="big.md", size_bytes=six_mb)
    assert accepted.status_code == 201, accepted.text[:300]
    assert accepted.json()["size_bytes"] == six_mb


@pytest.mark.asyncio
async def test_extensions_setting_changes_real_upload(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Список расширений из настройки действует на реальную загрузку."""
    await _login_admin(api_client, app_fixture, "limits-admin-ext@orqion.local")
    corpus_id = await _create_corpus(app_fixture, "limits-ext")

    assert (
        await _upload(api_client, corpus_id, filename="note.txt", size_bytes=1024)
    ).status_code == (201)

    assert (await _patch(api_client, ALLOWED_UPLOAD_EXTENSIONS_KEY, ".md")).status_code == 200

    rejected = await _upload(api_client, corpus_id, filename="note2.txt", size_bytes=1024)
    assert rejected.status_code == 415
    body = rejected.json()
    assert body["constraint"]["allowed_extensions"] == [".md"]
    assert ".md" in body["hint"]

    assert (
        await _upload(api_client, corpus_id, filename="note.md", size_bytes=1024)
    ).status_code == (201)


@pytest.mark.asyncio
async def test_empty_extension_list_forbids_every_upload(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Пустой список — ноль разрешённых файлов, а не «разрешены все»."""
    await _login_admin(api_client, app_fixture, "limits-admin-empty@orqion.local")
    corpus_id = await _create_corpus(app_fixture, "limits-empty")

    assert (await _patch(api_client, ALLOWED_UPLOAD_EXTENSIONS_KEY, "")).status_code == 200

    rejected = await _upload(api_client, corpus_id, filename="any.md", size_bytes=1024)
    assert rejected.status_code == 415
    body = rejected.json()
    assert body["constraint"]["allowed_extensions"] == []
    assert "пуст" in body["hint"]


@pytest.mark.asyncio
async def test_invalid_values_rejected_and_limits_unchanged(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Границы из описания ключей действуют, отказ не меняет загрузку."""
    await _login_admin(api_client, app_fixture, "limits-admin-invalid@orqion.local")
    corpus_id = await _create_corpus(app_fixture, "limits-invalid")

    for value in (0, -1, 1025, "пять"):
        response = await _patch(api_client, MAX_UPLOAD_SIZE_KEY, value)
        assert response.status_code == 422, (value, response.text[:200])
        assert response.json()["error"] == "setting_value_invalid"

    # Расширение без точки: совпадение проверяется по концу имени файла.
    dotted = await _patch(api_client, ALLOWED_UPLOAD_EXTENSIONS_KEY, "pdf,md")
    assert dotted.status_code == 422
    assert "точк" in dotted.json()["hint"]

    too_long = await _patch(api_client, ALLOWED_UPLOAD_EXTENSIONS_KEY, ".ab" * 400)
    assert too_long.status_code == 422

    accepted = await _upload(api_client, corpus_id, filename="still-ok.md", size_bytes=4096)
    assert accepted.status_code == 201, accepted.text[:300]
    rejected = await _upload(
        api_client, corpus_id, filename="still-big.md", size_bytes=6 * BYTES_PER_MB
    )
    assert rejected.status_code == 413


@pytest.mark.asyncio
async def test_role_without_right_cannot_change_limits(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Без способности на ключ — 404, поле только для чтения, загрузка прежняя."""
    developer = await _seed_user(
        app_fixture, role_name="developer", email="limits-dev@orqion.local"
    )
    api_client.cookies.clear()
    login = await api_client.post(
        LOGIN_PATH, json={"email": developer[0], "password": developer[1]}
    )
    assert login.status_code == 200, login.text[:300]

    for key, value in ((MAX_UPLOAD_SIZE_KEY, 1), (ALLOWED_UPLOAD_EXTENSIONS_KEY, ".md")):
        response = await _patch(api_client, key, value)
        assert response.status_code == 404, response.text[:300]

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    for key in (MAX_UPLOAD_SIZE_KEY, ALLOWED_UPLOAD_EXTENSIONS_KEY):
        entry = _entry(entries, key)
        assert entry["editable"] is False
        assert entry["source"] == "default"

    # Право на загрузку документов у роли есть — ограничение осталось env-ным.
    # Имя корпуса из списка видимости роли developer.
    corpus_id = await _create_corpus(app_fixture, "public")
    accepted = await _upload(api_client, corpus_id, filename="dev.txt", size_bytes=2048)
    assert accepted.status_code == 201, accepted.text[:300]
    rejected = await _upload(api_client, corpus_id, filename="dev.md", size_bytes=6 * BYTES_PER_MB)
    assert rejected.status_code == 413
    assert rejected.json()["constraint"]["max_size_bytes"] == ENV_SIZE_MB * BYTES_PER_MB


@pytest.mark.asyncio
async def test_settings_survive_new_login_of_same_process(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Значение читается на каждый запрос, а не запоминается при старте."""
    await _login_admin(api_client, app_fixture, "limits-admin-relogin@orqion.local")
    corpus_id = await _create_corpus(app_fixture, "limits-relogin")
    assert (await _patch(api_client, MAX_UPLOAD_SIZE_KEY, 1)).status_code == 200

    await _login_admin(api_client, app_fixture, "limits-admin-relogin2@orqion.local")

    rejected = await _upload(
        api_client, corpus_id, filename="relogin.md", size_bytes=2 * BYTES_PER_MB
    )
    assert rejected.status_code == 413


# ---------------------------------------------------------------------------
# Импорт git-репозитория: тот же действующий предел
# ---------------------------------------------------------------------------


def _create_repo(repo_dir: str, files: dict[str, bytes]) -> str:
    """Локальный git-репозиторий с указанными файлами."""
    from dulwich import porcelain

    porcelain.init(repo_dir)
    for rel_path, content in files.items():
        full_path = os.path.join(repo_dir, *rel_path.split("/"))
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as handle:
            handle.write(content)
        porcelain.add(repo_dir, [rel_path.encode()])
    porcelain.commit(
        repo_dir,
        message=b"Limits probe",
        author=b"Probe <probe@orqion.local>",
        committer=b"Probe <probe@orqion.local>",
    )
    return repo_dir


def _cli_settings(test_settings: Settings, size_mb: int, extensions: str) -> EnvFreeSettings:
    """Настройки CLI на той же БД и том же хранилище, что у приложения."""
    return EnvFreeSettings(
        database_url=test_settings.database_url,
        blob_store_path=test_settings.blob_store_path,
        vector_store_path=test_settings.vector_store_path,
        log_level="WARNING",
        max_upload_size_mb=size_mb,
        allowed_upload_extensions=extensions,
    )


@pytest.mark.asyncio
async def test_git_import_reads_the_same_resolved_values(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    test_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Оба пути попадания файла в корпус читают одно значение настройки."""
    from app.cli import _run_ingest_git

    await _login_admin(api_client, app_fixture, "limits-admin-git@orqion.local")
    corpus_id = await _create_corpus(app_fixture, "limits-git-http")
    assert (await _patch(api_client, MAX_UPLOAD_SIZE_KEY, 2)).status_code == 200
    assert (await _patch(api_client, ALLOWED_UPLOAD_EXTENSIONS_KEY, ".md")).status_code == 200

    # Те же файлы тем же путём, которым их принёс бы пользователь.
    http_txt = await _upload(api_client, corpus_id, filename="notes.txt", size_bytes=1024)
    assert http_txt.status_code == 415
    big_content = b"# big\n" + b"x" * (3 * BYTES_PER_MB)
    http_big = await _upload(api_client, corpus_id, filename="big.md", size_bytes=len(big_content))
    assert http_big.status_code == 413

    repo_dir = _create_repo(
        str(tmp_path / "limits-repo"),
        {
            "notes.md": b"# notes\nsmall markdown\n",
            "notes.txt": b"plain text file\n",
            "big.md": big_content,
        },
    )
    monkeypatch.setattr(
        "app.cli.Settings",
        lambda: _cli_settings(test_settings, ENV_SIZE_MB, ENV_EXTENSIONS),
    )

    await _run_ingest_git(
        url=repo_dir,
        corpus_name="limits-git-cli",
        extensions_str=None,
        depth=1,
        clone_timeout=60,
        max_clone_size=200,
        max_file_size=None,
        build_index=False,
    )

    printed = capsys.readouterr().out
    assert "File size limit: 2 MB (from workspace settings)" in printed
    assert "Extensions: .md (from workspace settings)" in printed
    # .txt не собран вовсе, notes.md загружен, big.md отклонён тем же пределом.
    assert "Total files: 2" in printed
    assert "Ingested: 1" in printed
    assert "Failed: 1" in printed
    assert "big.md" in printed


@pytest.mark.asyncio
async def test_cli_without_flags_uses_workspace_settings(
    test_engine: AsyncEngine,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Без явных флагов импорт берёт ограничения из настроек рабочей области."""
    from app.cli import _run_ingest_git

    settings = _cli_settings(test_settings, ENV_SIZE_MB, ENV_EXTENSIONS)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)
    captured, workspace_id = await _prepare_workspace_rows(
        test_engine,
        monkeypatch,
        size_mb=3,
        extensions=".md, .sql",
    )

    await _run_ingest_git(**_cli_args())

    kwargs = captured[0]
    assert kwargs["workspace_id"] == workspace_id
    assert kwargs["max_file_size_bytes"] == 3 * BYTES_PER_MB
    assert kwargs["allowed_extensions"] == (".md", ".sql")
    printed = capsys.readouterr().out
    assert "File size limit: 3 MB (from workspace settings)" in printed
    assert "Extensions: .md, .sql (from workspace settings)" in printed


@pytest.mark.asyncio
async def test_cli_explicit_flags_override_settings(
    test_engine: AsyncEngine,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Явный флаг действует как явный: источник значения печатается."""
    from app.cli import _run_ingest_git

    settings = _cli_settings(test_settings, ENV_SIZE_MB, ENV_EXTENSIONS)
    monkeypatch.setattr("app.cli.Settings", lambda: settings)
    captured, _ = await _prepare_workspace_rows(
        test_engine, monkeypatch, size_mb=3, extensions=".md"
    )

    await _run_ingest_git(**_cli_args(max_file_size=7, extensions_str=".py,.go"))

    kwargs = captured[0]
    assert kwargs["max_file_size_bytes"] == 7 * BYTES_PER_MB
    assert kwargs["allowed_extensions"] == (".go", ".py")
    printed = capsys.readouterr().out
    assert "File size limit: 7 MB (from --max-file-size)" in printed
    assert "Extensions: .go, .py (from --extensions)" in printed


def _cli_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "url": "https://git.example.invalid/probe.git",
        "corpus_name": "cli-probe",
        "extensions_str": None,
        "depth": 1,
        "clone_timeout": 30,
        "max_clone_size": 100,
        "max_file_size": None,
        "build_index": False,
    }
    args.update(overrides)
    return args


async def _prepare_workspace_rows(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    size_mb: int,
    extensions: str,
) -> tuple[list[dict[str, Any]], str]:
    """Записи настроек в базе теста и перехват аргументов импорта.

    Движок берётся из фикстуры ``test_engine``, а не создаётся своим: только
    фикстура регистрирует очистку базы после теста. На общей базе (прогон
    против PostgreSQL) собственный движок оставлял записи следующему тесту,
    и вторая вставка того же ключа падала на нарушении первичного ключа.

    Сам импорт подменяется: проверяется резолв ограничений, а не клонирование.
    """
    from app.db.engine import create_session_factory
    from app.db.models import WorkspaceSetting
    from app.db.workspace import ensure_default_workspace

    factory = create_session_factory(engine)
    async with factory() as session:
        workspace_id = await ensure_default_workspace(session)
        session.add_all(
            [
                WorkspaceSetting(
                    workspace_id=workspace_id,
                    key=MAX_UPLOAD_SIZE_KEY,
                    value=size_mb,
                ),
                WorkspaceSetting(
                    workspace_id=workspace_id,
                    key=ALLOWED_UPLOAD_EXTENSIONS_KEY,
                    value=extensions,
                ),
            ]
        )
        await session.commit()

    captured: list[dict[str, Any]] = []

    async def _fake_ingest(*args: Any, **kwargs: Any) -> GitIngestResult:
        captured.append(kwargs)
        return GitIngestResult(total_files=0, ingested=0, skipped=0, failed=0, errors=[])

    monkeypatch.setattr("app.rag.git_ingest.ingest_git_repository", _fake_ingest)
    return captured, workspace_id
