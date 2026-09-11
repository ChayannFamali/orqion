"""Ограничения загрузки как настройка рабочей области.

Проверяется не «значение читается из таблицы», а то, от чего оно зависит:
разбор списка расширений, порядок резолва (запись в БД побеждает
env-дефолт), область действия и отказ в безопасную сторону при испорченной
записи. Отдельно — что оба ключа принадлежат одной категории интерфейса и
пишутся под одним правом.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.models import Role, User, Workspace, WorkspaceSetting
from app.settings.registry import (
    ALLOWED_UPLOAD_EXTENSIONS_KEY,
    DEFAULT_WRITE_CAPABILITY,
    FILES_CATEGORY,
    MAX_UPLOAD_SIZE_KEY,
    MAX_UPLOAD_SIZE_MB_LIMIT,
    describe_value_field,
    get_spec,
)
from app.settings.uploads import BYTES_PER_MB, UploadLimits, parse_extensions, read_upload_limits
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed(db_session: AsyncSession, name: str = "uploads-unit") -> str:
    """Рабочая область, роль и пользователь; возвращает id рабочей области."""
    workspace = Workspace(name=name)
    db_session.add(workspace)
    await db_session.flush()
    role = Role(
        workspace_id=workspace.id,
        name=f"{name}-role",
        is_builtin=False,
        policy={"models": ["*"], "corpora": ["*"]},
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        User(
            workspace_id=workspace.id,
            email=f"{name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
    )
    await db_session.flush()
    return workspace.id


async def _write(db_session: AsyncSession, workspace_id: str, key: str, value: object) -> None:
    """Пишет значение ключа: вставка или правка существующей строки."""
    row = await db_session.get(WorkspaceSetting, (workspace_id, key))
    if row is None:
        db_session.add(
            WorkspaceSetting(
                workspace_id=workspace_id,
                key=key,
                value=value,
                updated_at=datetime.now(UTC),
            )
        )
    else:
        row.value = value
        row.updated_at = datetime.now(UTC)
    await db_session.flush()


def _env(size_mb: int = 5, extensions: str = ".md,.txt") -> Settings:
    """Настройки с заданными env-значениями.

    Явные аргументы важнее переменных окружения и ``.env``, поэтому проверка
    не зависит от того, что задано на машине разработчика.
    """
    return Settings(max_upload_size_mb=size_mb, allowed_upload_extensions=extensions)


# ---------------------------------------------------------------------------
# parse_extensions
# ---------------------------------------------------------------------------


def test_parse_extensions_returns_sorted_dotted_lowercased() -> None:
    assert parse_extensions(".MD, .txt ,.pdf") == (".md", ".pdf", ".txt")


def test_parse_extensions_collapses_duplicates() -> None:
    assert parse_extensions(".md,.MD,.md") == (".md",)


def test_parse_extensions_empty_string_forbids_everything() -> None:
    """Пустой список — ноль разрешённых файлов, а не «разрешены все»."""
    assert parse_extensions("") == ()
    assert parse_extensions(" , ,") == ()


def test_parse_extensions_adds_missing_dot_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Env-конфиг не валидируется: элемент без точки приводится к виду с точкой."""
    with caplog.at_level(logging.WARNING, logger="app.settings.uploads"):
        parsed = parse_extensions("md,.txt")

    assert parsed == (".md", ".txt")
    assert "без ведущей точки" in caplog.text


def test_parse_extensions_no_warning_for_well_formed_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.settings.uploads"):
        parse_extensions(".md,.txt")

    assert caplog.text == ""


# ---------------------------------------------------------------------------
# Реестр: оба ключа зарегистрированы и описаны
# ---------------------------------------------------------------------------


def test_both_keys_registered_in_files_category() -> None:
    for key in (MAX_UPLOAD_SIZE_KEY, ALLOWED_UPLOAD_EXTENSIONS_KEY):
        spec = get_spec(key)
        assert spec is not None
        assert spec.category == FILES_CATEGORY
        assert spec.write_capability == DEFAULT_WRITE_CAPABILITY


def test_size_field_is_bounded_integer() -> None:
    spec = get_spec(MAX_UPLOAD_SIZE_KEY)
    assert spec is not None
    field = describe_value_field(spec)
    assert field.type == "integer"
    assert field.min == 1
    assert field.max == MAX_UPLOAD_SIZE_MB_LIMIT


def test_extensions_field_is_string() -> None:
    spec = get_spec(ALLOWED_UPLOAD_EXTENSIONS_KEY)
    assert spec is not None
    field = describe_value_field(spec)
    assert field.type == "string"


def test_size_value_rejects_zero_and_overlimit() -> None:
    spec = get_spec(MAX_UPLOAD_SIZE_KEY)
    assert spec is not None
    for bad in (0, -1, MAX_UPLOAD_SIZE_MB_LIMIT + 1, "много"):
        with pytest.raises(ValidationError):
            spec.value_model.model_validate({"value": bad})


def test_extensions_value_requires_leading_dot() -> None:
    """Совпадение проверяется по концу имени, «pdf» без точки не совпало бы ни с чем."""
    spec = get_spec(ALLOWED_UPLOAD_EXTENSIONS_KEY)
    assert spec is not None
    with pytest.raises(ValidationError, match="с точкой"):
        spec.value_model.model_validate({"value": "pdf,md"})


def test_extensions_value_accepts_dotted_csv_and_empty() -> None:
    spec = get_spec(ALLOWED_UPLOAD_EXTENSIONS_KEY)
    assert spec is not None
    for good in (".md,.txt", ".pdf", ""):
        dumped = spec.value_model.model_validate({"value": good}).model_dump()
        assert dumped["value"] == good


def test_extensions_value_tolerates_spaces_around_items() -> None:
    """Пробел после запятой — оформление списка, а не другое расширение."""
    spec = get_spec(ALLOWED_UPLOAD_EXTENSIONS_KEY)
    assert spec is not None
    dumped = spec.value_model.model_validate({"value": ".md, .sql ,\t.txt"}).model_dump()
    assert dumped["value"] == ".md, .sql ,\t.txt"


# ---------------------------------------------------------------------------
# read_upload_limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_defaults_when_no_db_rows(db_session: AsyncSession) -> None:
    workspace_id = await _seed(db_session)

    limits = await read_upload_limits(
        db_session, workspace_id, app_settings=_env(size_mb=5, extensions=".md,.txt")
    )

    assert limits == UploadLimits(
        max_file_size_bytes=5 * BYTES_PER_MB,
        allowed_extensions=(".md", ".txt"),
    )


@pytest.mark.asyncio
async def test_db_rows_win_over_env_defaults(db_session: AsyncSession) -> None:
    workspace_id = await _seed(db_session)
    await _write(db_session, workspace_id, MAX_UPLOAD_SIZE_KEY, 2)
    await _write(db_session, workspace_id, ALLOWED_UPLOAD_EXTENSIONS_KEY, ".md")

    limits = await read_upload_limits(
        db_session, workspace_id, app_settings=_env(size_mb=5, extensions=".txt,.pdf")
    )

    assert limits.max_file_size_bytes == 2 * BYTES_PER_MB
    assert limits.allowed_extensions == (".md",)


@pytest.mark.asyncio
async def test_each_key_resolves_independently(db_session: AsyncSession) -> None:
    """Запись только одного ключа не меняет второй."""
    workspace_id = await _seed(db_session)
    await _write(db_session, workspace_id, MAX_UPLOAD_SIZE_KEY, 3)

    limits = await read_upload_limits(
        db_session, workspace_id, app_settings=_env(size_mb=5, extensions=".md,.txt")
    )

    assert limits.max_file_size_bytes == 3 * BYTES_PER_MB
    assert limits.allowed_extensions == (".md", ".txt")


@pytest.mark.asyncio
async def test_limits_scoped_to_own_workspace(db_session: AsyncSession) -> None:
    first = await _seed(db_session, "uploads-first")
    second = await _seed(db_session, "uploads-second")
    await _write(db_session, first, MAX_UPLOAD_SIZE_KEY, 1)
    await _write(db_session, first, ALLOWED_UPLOAD_EXTENSIONS_KEY, ".md")

    other = await read_upload_limits(
        db_session, second, app_settings=_env(size_mb=5, extensions=".txt")
    )

    assert other.max_file_size_bytes == 5 * BYTES_PER_MB
    assert other.allowed_extensions == (".txt",)


@pytest.mark.asyncio
async def test_invalid_size_row_falls_back_to_env_default(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Испорченная запись не блокирует загрузку: действует env-дефолт."""
    workspace_id = await _seed(db_session)
    await _write(db_session, workspace_id, MAX_UPLOAD_SIZE_KEY, 5000)

    with caplog.at_level(logging.WARNING, logger="app.settings.service"):
        limits = await read_upload_limits(
            db_session, workspace_id, app_settings=_env(size_mb=5, extensions=".md")
        )

    assert limits.max_file_size_bytes == 5 * BYTES_PER_MB
    assert MAX_UPLOAD_SIZE_KEY in caplog.text


@pytest.mark.asyncio
async def test_invalid_extensions_row_falls_back_to_env_default(db_session: AsyncSession) -> None:
    workspace_id = await _seed(db_session)
    await _write(db_session, workspace_id, ALLOWED_UPLOAD_EXTENSIONS_KEY, "pdf")

    limits = await read_upload_limits(
        db_session, workspace_id, app_settings=_env(size_mb=5, extensions=".md,.txt")
    )

    assert limits.allowed_extensions == (".md", ".txt")


@pytest.mark.asyncio
async def test_ambient_settings_used_when_not_passed(db_session: AsyncSession) -> None:
    """Без явных настроек берётся ``Settings()`` из окружения процесса."""
    workspace_id = await _seed(db_session)
    ambient = Settings()

    limits = await read_upload_limits(db_session, workspace_id)

    assert limits.max_file_size_bytes == ambient.max_upload_size_mb * BYTES_PER_MB
    assert limits.allowed_extensions == parse_extensions(ambient.allowed_upload_extensions)


@pytest.mark.asyncio
async def test_limits_are_immutable(db_session: AsyncSession) -> None:
    """Значение уходит в два разных пути загрузки — менять его на месте нельзя."""
    workspace_id = await _seed(db_session)

    limits = await read_upload_limits(db_session, workspace_id, app_settings=_env())

    with pytest.raises(AttributeError):
        limits.max_file_size_bytes = 1  # type: ignore[misc]
