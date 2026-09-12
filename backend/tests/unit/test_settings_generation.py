"""Дефолт температуры генерации как настройка рабочей области.

Проверяется то, от чего зависит значение на бою: границы ключа (нерабочее
значение не должно сохраняться), порядок резолва (запись в БД побеждает
env-дефолт), область действия и отказ в безопасную сторону при испорченной
записи. Отдельно — что значение всегда возвращается ``float``: потребитель
читает его через ``read_setting_as`` с проверкой типа.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from app.auth.passwords import hash_password
from app.config import Settings
from app.db.models import Role, User, Workspace, WorkspaceSetting
from app.settings.generation import read_default_temperature
from app.settings.registry import (
    DEFAULT_TEMPERATURE_KEY,
    DEFAULT_WRITE_CAPABILITY,
    GENERATION_CATEGORY,
    TEMPERATURE_MAX,
    describe_value_field,
    get_spec,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed(db_session: AsyncSession, name: str = "generation-unit") -> str:
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


async def _write(db_session: AsyncSession, workspace_id: str, value: object) -> None:
    """Пишет значение ключа температуры: вставка или правка существующей строки."""
    row = await db_session.get(WorkspaceSetting, (workspace_id, DEFAULT_TEMPERATURE_KEY))
    if row is None:
        db_session.add(
            WorkspaceSetting(
                workspace_id=workspace_id,
                key=DEFAULT_TEMPERATURE_KEY,
                value=value,
                updated_at=datetime.now(UTC),
            )
        )
    else:
        row.value = value
        row.updated_at = datetime.now(UTC)
    await db_session.flush()


def _env(temperature: float = 0.42) -> Settings:
    """Настройки с заданным env-значением.

    Явный аргумент важнее переменных окружения и ``.env``, поэтому проверка
    не зависит от того, что задано на машине разработчика.
    """
    return Settings(default_temperature=temperature)


# ---------------------------------------------------------------------------
# Реестр: ключ зарегистрирован и описан
# ---------------------------------------------------------------------------


def test_key_registered_in_generation_category() -> None:
    spec = get_spec(DEFAULT_TEMPERATURE_KEY)
    assert spec is not None
    assert spec.category == GENERATION_CATEGORY
    assert spec.write_capability == DEFAULT_WRITE_CAPABILITY
    assert spec.default_from_env == DEFAULT_TEMPERATURE_KEY


def test_field_is_bounded_number() -> None:
    """Граница 0–2 — рабочий диапазон OpenAI-совместимых API, не выдуманное число."""
    spec = get_spec(DEFAULT_TEMPERATURE_KEY)
    assert spec is not None
    field = describe_value_field(spec)
    assert field.type == "number"
    assert field.min == 0.0
    assert field.max == TEMPERATURE_MAX


def test_value_rejects_out_of_range_and_non_numeric() -> None:
    """Значение выше 2 дало бы отказ апстрима, то есть нерабочий чат."""
    spec = get_spec(DEFAULT_TEMPERATURE_KEY)
    assert spec is not None
    for bad in (TEMPERATURE_MAX + 0.5, -0.1, "тепло"):
        with pytest.raises(ValidationError):
            spec.value_model.model_validate({"value": bad})


def test_value_accepts_bounds() -> None:
    spec = get_spec(DEFAULT_TEMPERATURE_KEY)
    assert spec is not None
    for good in (0.0, TEMPERATURE_MAX, 0.35):
        assert spec.value_model.model_validate({"value": good}).model_dump()["value"] == good


def test_integer_value_is_coerced_to_float() -> None:
    """Целое «1» из интерфейса становится float: потребитель ждёт ровно float."""
    spec = get_spec(DEFAULT_TEMPERATURE_KEY)
    assert spec is not None
    value = spec.value_model.model_validate({"value": 1}).model_dump(mode="json")["value"]
    assert value == 1.0
    assert isinstance(value, float)


# ---------------------------------------------------------------------------
# read_default_temperature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_default_when_no_db_row(db_session: AsyncSession) -> None:
    workspace_id = await _seed(db_session)

    value = await read_default_temperature(
        db_session, workspace_id, app_settings=_env(temperature=0.42)
    )

    assert value == 0.42


@pytest.mark.asyncio
async def test_db_row_wins_over_env_default(db_session: AsyncSession) -> None:
    workspace_id = await _seed(db_session)
    await _write(db_session, workspace_id, 1.25)

    value = await read_default_temperature(
        db_session, workspace_id, app_settings=_env(temperature=0.42)
    )

    assert value == 1.25


@pytest.mark.asyncio
async def test_value_scoped_to_own_workspace(db_session: AsyncSession) -> None:
    first = await _seed(db_session, "generation-first")
    second = await _seed(db_session, "generation-second")
    await _write(db_session, first, 1.75)

    other = await read_default_temperature(db_session, second, app_settings=_env(temperature=0.42))

    assert other == 0.42


@pytest.mark.asyncio
async def test_out_of_range_row_falls_back_to_env_default(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Испорченная запись не блокирует чат: действует env-дефолт."""
    workspace_id = await _seed(db_session)
    await _write(db_session, workspace_id, 5.0)

    with caplog.at_level(logging.WARNING, logger="app.settings.service"):
        value = await read_default_temperature(
            db_session, workspace_id, app_settings=_env(temperature=0.42)
        )

    assert value == 0.42
    assert DEFAULT_TEMPERATURE_KEY in caplog.text


@pytest.mark.asyncio
async def test_non_numeric_row_falls_back_to_env_default(db_session: AsyncSession) -> None:
    workspace_id = await _seed(db_session)
    await _write(db_session, workspace_id, "тёплый ответ")

    value = await read_default_temperature(
        db_session, workspace_id, app_settings=_env(temperature=0.42)
    )

    assert value == 0.42


@pytest.mark.asyncio
async def test_integer_row_returned_as_float(db_session: AsyncSession) -> None:
    """Запись целого числа проходит проверку типа потребителя."""
    workspace_id = await _seed(db_session)
    await _write(db_session, workspace_id, 2)

    value = await read_default_temperature(
        db_session, workspace_id, app_settings=_env(temperature=0.42)
    )

    assert value == 2.0
    assert type(value) is float


@pytest.mark.asyncio
async def test_ambient_settings_used_when_not_passed(db_session: AsyncSession) -> None:
    """Без явных настроек берётся ``Settings()`` из окружения процесса."""
    workspace_id = await _seed(db_session)
    ambient = Settings()

    value = await read_default_temperature(db_session, workspace_id)

    assert value == ambient.default_temperature


@pytest.mark.asyncio
async def test_zero_disables_randomness(db_session: AsyncSession) -> None:
    """Ноль — рабочее значение, а не «не задано»: отличать их обязан тип."""
    workspace_id = await _seed(db_session)
    await _write(db_session, workspace_id, 0.0)

    value = await read_default_temperature(
        db_session, workspace_id, app_settings=_env(temperature=0.42)
    )

    assert value == 0.0
