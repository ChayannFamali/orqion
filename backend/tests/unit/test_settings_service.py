"""Резолв значений служебных настроек для исполняющего кода.

Проверяется не «значение читается из таблицы», а то, что от этого зависит:
порядок резолва (запись в БД побеждает env-дефолт), область действия
(только своя рабочая область), отказ в безопасную сторону при испорченной
записи и фактический срок новых сессий.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import create_session
from app.config import Settings
from app.db.models import Role, User, Workspace, WorkspaceSetting
from app.db.models import Session as SessionModel
from app.settings.registry import SESSION_TTL_KEY, SETTINGS_REGISTRY, SettingSpec
from app.settings.service import read_setting, read_setting_as
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: Ключи, которых нет ни в реестре, ни в миграциях: ими проверяются резолв
#: нечисловых значений и отказ при несовпадении типа.
STR_KEY = "probe_text_setting"
BOOL_KEY = "probe_flag_setting"


class StrValue(BaseModel):
    value: str = "дефолт"


class BoolValue(BaseModel):
    value: bool = False


@pytest.fixture(autouse=True)
def _probe_keys() -> Iterator[None]:
    """Регистрирует пробные ключи и убирает их после теста."""
    SETTINGS_REGISTRY[STR_KEY] = SettingSpec(
        key=STR_KEY,
        title="Пробная строка",
        description="Пробный ключ строкового типа",
        category="Общие",
        value_model=StrValue,
    )
    SETTINGS_REGISTRY[BOOL_KEY] = SettingSpec(
        key=BOOL_KEY,
        title="Пробный флаг",
        description="Пробный ключ булева типа",
        category="Общие",
        value_model=BoolValue,
    )
    yield
    SETTINGS_REGISTRY.pop(STR_KEY, None)
    SETTINGS_REGISTRY.pop(BOOL_KEY, None)


async def _seed(db_session: AsyncSession) -> tuple[str, str]:
    """Рабочая область, роль и пользователь; возвращает их id."""
    workspace = Workspace(name="settings-service-unit")
    db_session.add(workspace)
    await db_session.flush()
    role = Role(
        workspace_id=workspace.id,
        name="settings-service-role",
        is_builtin=False,
        policy={"models": ["*"], "corpora": ["*"]},
    )
    db_session.add(role)
    await db_session.flush()
    user = User(
        workspace_id=workspace.id,
        email="settings-service@orqion.local",
        password_hash=hash_password("pass-123"),
        role_id=role.id,
    )
    db_session.add(user)
    await db_session.flush()
    return workspace.id, user.id


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


def _as_utc(value: datetime) -> datetime:
    """SQLite отдаёт naive datetime для колонки с timezone=True."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ttl_settings(days: int) -> Settings:
    """Настройки с заданным env-значением срока сессии.

    Явный аргумент важнее переменных окружения и ``.env``, поэтому значение
    не зависит от того, что задано на машине разработчика.
    """
    return Settings(session_ttl_days=days)


# ---------------------------------------------------------------------------
# read_setting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_default_when_no_db_row(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    workspace_id, _ = await _seed(db_session)

    value = await read_setting(
        db_session, workspace_id, SESSION_TTL_KEY, app_settings=test_settings
    )
    assert value == test_settings.session_ttl_days


@pytest.mark.asyncio
async def test_db_row_wins_over_env_default(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    workspace_id, _ = await _seed(db_session)
    await _write(db_session, workspace_id, SESSION_TTL_KEY, 2)

    value = await read_setting(
        db_session, workspace_id, SESSION_TTL_KEY, app_settings=test_settings
    )
    assert value == 2


@pytest.mark.asyncio
async def test_other_workspace_row_is_not_visible(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    """Запись соседней рабочей области не меняет значение своей."""
    workspace_id, _ = await _seed(db_session)
    other = Workspace(name="settings-service-neighbour")
    db_session.add(other)
    await db_session.flush()
    await _write(db_session, other.id, SESSION_TTL_KEY, 2)

    value = await read_setting(
        db_session, workspace_id, SESSION_TTL_KEY, app_settings=test_settings
    )
    assert value == test_settings.session_ttl_days


@pytest.mark.asyncio
async def test_invalid_db_value_falls_back_to_env_default(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    """Испорченная запись не ломает исполняющий код и не расширяет значение.

    Через API такое значение пройти не может, остаётся правка базы руками;
    отказ здесь означал бы, что испорченная строка блокирует выдачу сессий.
    """
    workspace_id, _ = await _seed(db_session)
    await _write(db_session, workspace_id, SESSION_TTL_KEY, 999999)

    value = await read_setting(
        db_session, workspace_id, SESSION_TTL_KEY, app_settings=test_settings
    )
    assert value == test_settings.session_ttl_days


@pytest.mark.asyncio
async def test_unregistered_key_raises(db_session: AsyncSession) -> None:
    workspace_id, _ = await _seed(db_session)

    with pytest.raises(LookupError):
        await read_setting(db_session, workspace_id, "нет_такого_ключа")


@pytest.mark.asyncio
async def test_ambient_settings_used_when_not_passed(db_session: AsyncSession) -> None:
    workspace_id, _ = await _seed(db_session)

    value = await read_setting(db_session, workspace_id, SESSION_TTL_KEY)
    assert value == Settings().session_ttl_days


@pytest.mark.asyncio
async def test_string_key_resolves(db_session: AsyncSession) -> None:
    workspace_id, _ = await _seed(db_session)
    await _write(db_session, workspace_id, STR_KEY, "своё значение")

    assert await read_setting(db_session, workspace_id, STR_KEY) == "своё значение"


# ---------------------------------------------------------------------------
# read_setting_as
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_setting_as_returns_typed_value(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    workspace_id, _ = await _seed(db_session)
    await _write(db_session, workspace_id, SESSION_TTL_KEY, 3)

    value = await read_setting_as(
        db_session, workspace_id, SESSION_TTL_KEY, int, app_settings=test_settings
    )
    assert value == 3
    assert isinstance(value, int)


@pytest.mark.asyncio
async def test_read_setting_as_rejects_type_mismatch(db_session: AsyncSession) -> None:
    """Ключ описан строкой, потребитель ждёт число — отказ, а не приведение."""
    workspace_id, _ = await _seed(db_session)

    with pytest.raises(TypeError):
        await read_setting_as(db_session, workspace_id, STR_KEY, int)


@pytest.mark.asyncio
async def test_read_setting_as_does_not_confuse_bool_with_int(
    db_session: AsyncSession,
) -> None:
    """``isinstance(True, int)`` — поэтому проверка типа строгая, по ``type()``."""
    workspace_id, _ = await _seed(db_session)
    await _write(db_session, workspace_id, BOOL_KEY, True)

    assert await read_setting_as(db_session, workspace_id, BOOL_KEY, bool) is True
    with pytest.raises(TypeError):
        await read_setting_as(db_session, workspace_id, BOOL_KEY, int)


# ---------------------------------------------------------------------------
# create_session: фактический срок новых сессий
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_uses_env_default_without_db_row(
    db_session: AsyncSession,
) -> None:
    workspace_id, user_id = await _seed(db_session)

    session_id = await create_session(db_session, user_id, workspace_id, _ttl_settings(9))
    record = (
        await db_session.execute(select(SessionModel).where(SessionModel.id == session_id))
    ).scalar_one()

    delta = _as_utc(record.expires_at) - datetime.now(UTC)
    assert timedelta(days=9) - timedelta(minutes=5) < delta < timedelta(days=9)


@pytest.mark.asyncio
async def test_create_session_uses_db_value(
    db_session: AsyncSession,
) -> None:
    """Запись в реестре побеждает env-значение: 9 дней в конфиге, 2 в БД."""
    workspace_id, user_id = await _seed(db_session)
    await _write(db_session, workspace_id, SESSION_TTL_KEY, 2)

    session_id = await create_session(db_session, user_id, workspace_id, _ttl_settings(9))
    record = (
        await db_session.execute(select(SessionModel).where(SessionModel.id == session_id))
    ).scalar_one()

    delta = _as_utc(record.expires_at) - datetime.now(UTC)
    assert timedelta(days=2) - timedelta(minutes=5) < delta < timedelta(days=2)


@pytest.mark.asyncio
async def test_setting_change_does_not_touch_issued_sessions(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    """Настройка действует на новые сессии; выданные остаются со своим сроком."""
    workspace_id, user_id = await _seed(db_session)
    await _write(db_session, workspace_id, SESSION_TTL_KEY, 10)
    old_id = await create_session(db_session, user_id, workspace_id, test_settings)
    old_expires = _as_utc(
        (await db_session.execute(select(SessionModel).where(SessionModel.id == old_id)))
        .scalar_one()
        .expires_at
    )

    await _write(db_session, workspace_id, SESSION_TTL_KEY, 1)
    new_id = await create_session(db_session, user_id, workspace_id, test_settings)

    rows = (
        (
            await db_session.execute(
                select(SessionModel).where(SessionModel.id.in_([old_id, new_id]))
            )
        )
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in rows}
    assert _as_utc(by_id[old_id].expires_at) == old_expires
    new_delta = _as_utc(by_id[new_id].expires_at) - datetime.now(UTC)
    assert timedelta(days=1) - timedelta(minutes=5) < new_delta < timedelta(days=1)
