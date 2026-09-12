"""API личных настроек пользователя — раздел «Профиль» (Т-512).

GET /api/profile/preferences — все зарегистрированные ключи с резолвнутым
значением (запись владельца или дефолт модели), описанием поля для
интерфейса и подписями вариантов перечисления.

PATCH /api/profile/preferences — один ключ за вызов: значение проверяется
моделью спеки (422), неизвестный ключ — 404.

Два свойства этого раздела, которых нет у служебных настроек, и ради
которых написаны отдельные тесты:

- доступ — владение строкой, а не способность роли: читать и менять свою
  настройку может пользователь без единого права, а ``editable`` всегда
  ``true``;
- аудит не пишется: личное содержимое, как шаблоны промптов (Т-507). В
  служебных настройках аудит пишется на каждую запись, поэтому слепое
  копирование того маршрута добавило бы аудит незаметно — здесь проверяется
  равенство числа записей до и после успешной записи.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db import models as app_models
from app.db.models import AuditLog, Role, User, UserPreference
from app.policy.models import Policy
from app.preferences.registry import (
    CHAT_SEND_KEY,
    PREFERENCES_REGISTRY,
    PreferenceSpec,
)
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select

PREFERENCES_PATH = "/api/profile/preferences"

# Имя ключа, которого нет ни в одной миграции: им проверяется, что новая
# личная настройка появляется одним описанием в реестре.
RUNTIME_KEY = "runtime_only_preference"

#: Прод-состав реестра: фикстура ``registry`` очищает словарь, поэтому
#: образцы ключей для тестов берутся отсюда, а не из очищенного реестра.
_PROD_SPECS: dict[str, PreferenceSpec] = dict(PREFERENCES_REGISTRY)


class ProbeValue(BaseModel):
    value: int = 7


# Настоящие настройки для создания сессии: фикстура autouse заполняет
# держатель до тела теста.
_ACTIVE_SETTINGS: list[Settings] = []


@pytest.fixture(autouse=True)
def _capture_test_settings(test_settings: Settings) -> None:
    _ACTIVE_SETTINGS.clear()
    _ACTIVE_SETTINGS.append(test_settings)


@pytest.fixture
def registry() -> Any:
    """Временно очищает реестр: тесты не должны оставлять в нём ключей."""
    saved = dict(PREFERENCES_REGISTRY)
    PREFERENCES_REGISTRY.clear()
    yield PREFERENCES_REGISTRY
    PREFERENCES_REGISTRY.clear()
    PREFERENCES_REGISTRY.update(saved)


async def _login(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    *,
    name: str = "owner",
    capabilities: list[str] | None = None,
) -> str:
    """Сессия пользователя; возвращает его id.

    ``capabilities=None`` — роль без единого права: раздел обязан работать и
    для неё, потому что доступ здесь — владение строкой, а не способность.
    """
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    settings: Settings = _ACTIVE_SETTINGS[0]
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name=f"profile-{name}",
            is_builtin=False,
            policy=Policy(
                models=["*"], corpora=["*"], capabilities=capabilities or []
            ).model_dump(),
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email=f"profile-{name}@orqion.local",
            password_hash=hash_password("profile-pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, settings)
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _rows(app_fixture: FastAPI) -> list[UserPreference]:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        result = await session.execute(
            select(UserPreference).where(UserPreference.workspace_id == workspace_id)
        )
        return list(result.scalars().all())


async def _audit_count(app_fixture: FastAPI) -> int:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(AuditLog)) or 0)


async def _get_by_key(api_client: httpx.AsyncClient, key: str) -> dict[str, Any]:
    entries = (await api_client.get(PREFERENCES_PATH)).json()["preferences"]
    return next(entry for entry in entries if entry["key"] == key)


# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_requires_auth(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get(PREFERENCES_PATH)).status_code == 401


@pytest.mark.asyncio
async def test_patch_requires_auth(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "enter"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_lists_registered_keys_with_defaults(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.get(PREFERENCES_PATH)
    assert resp.status_code == 200
    entries = resp.json()["preferences"]
    assert {entry["key"] for entry in entries} == set(PREFERENCES_REGISTRY)

    entry = next(item for item in entries if item["key"] == CHAT_SEND_KEY)
    assert entry["value"] == "enter"
    assert entry["source"] == "default"
    assert entry["editable"] is True


@pytest.mark.asyncio
async def test_get_describes_field_for_ui(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """По ключу отдано всё, что нужно нарисовать поле без знания самого ключа."""
    await _login(api_client, app_fixture)

    entry = await _get_by_key(api_client, CHAT_SEND_KEY)
    assert entry["type"] == "enum"
    assert entry["enum_values"] == ["enter", "shift_enter"]
    assert set(entry["enum_labels"]) == {"enter", "shift_enter"}
    assert entry["title"] == "Отправка сообщения"
    assert entry["category"] == "Чат"
    assert entry["description"]
    # Машинное значение в списке выбора не показывается: подпись объясняет
    # поведение обеими комбинациями клавиш.
    for value, label in entry["enum_labels"].items():
        assert label != value
        assert "Enter" in label


@pytest.mark.asyncio
async def test_get_readable_without_any_capability(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Право на личную настройку — не способность роли: роль без прав читает."""
    await _login(api_client, app_fixture, name="no-rights", capabilities=[])

    resp = await api_client.get(PREFERENCES_PATH)
    assert resp.status_code == 200
    assert all(entry["editable"] is True for entry in resp.json()["preferences"])


@pytest.mark.asyncio
async def test_get_is_sorted_by_category_then_key(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    registry: dict[str, PreferenceSpec],
) -> None:
    registry[CHAT_SEND_KEY] = _PROD_SPECS[CHAT_SEND_KEY]
    registry["z_late_key"] = PreferenceSpec(
        key="z_late_key",
        title="Поздний ключ",
        description="Описание",
        category="Альфа",
        value_model=ProbeValue,
    )
    registry["a_early_key"] = PreferenceSpec(
        key="a_early_key",
        title="Ранний ключ",
        description="Описание",
        category="Альфа",
        value_model=ProbeValue,
    )
    await _login(api_client, app_fixture)

    entries = (await api_client.get(PREFERENCES_PATH)).json()["preferences"]
    pairs = [(entry["category"], entry["key"]) for entry in entries]
    assert pairs == sorted(pairs)
    assert len(pairs) == len(set(pairs))


@pytest.mark.asyncio
async def test_value_of_unregistered_key_is_not_listed(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Строка ключа, снятого с регистрации, не создаёт необъяснимое поле."""
    user_id = await _login(api_client, app_fixture)
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        session.add(
            UserPreference(
                workspace_id=workspace_id,
                user_id=user_id,
                key="legacy_removed_key",
                value="что угодно",
            )
        )
        await session.commit()

    entries = (await api_client.get(PREFERENCES_PATH)).json()["preferences"]
    assert "legacy_removed_key" not in {entry["key"] for entry in entries}


# ---------------------------------------------------------------------------
# PATCH — запись
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_writes_value_and_marks_source_db(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(
        PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "shift_enter"}
    )
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    assert body["key"] == CHAT_SEND_KEY
    assert body["value"] == "shift_enter"
    assert body["source"] == "db"

    rows = await _rows(app_fixture)
    assert len(rows) == 1
    assert rows[0].value == "shift_enter"

    entry = await _get_by_key(api_client, CHAT_SEND_KEY)
    assert (entry["value"], entry["source"]) == ("shift_enter", "db")


@pytest.mark.asyncio
async def test_patch_records_owner_and_workspace(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    user_id = await _login(api_client, app_fixture)

    await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "shift_enter"})

    rows = await _rows(app_fixture)
    assert rows[0].user_id == user_id
    assert rows[0].workspace_id == app_fixture.state.workspace_id
    assert rows[0].updated_at is not None


@pytest.mark.asyncio
async def test_patch_keeps_single_row_per_key(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "shift_enter"})
    await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "enter"})

    rows = await _rows(app_fixture)
    assert len(rows) == 1
    assert rows[0].value == "enter"


@pytest.mark.asyncio
async def test_patch_allowed_without_any_capability(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Роль без единого права меняет свою настройку: гейт — владение строкой."""
    await _login(api_client, app_fixture, name="no-rights", capabilities=[])

    resp = await api_client.patch(
        PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "shift_enter"}
    )
    assert resp.status_code == 200, resp.text[:300]
    assert (await _rows(app_fixture))[0].value == "shift_enter"


# ---------------------------------------------------------------------------
# PATCH — отказы
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_unknown_key_404(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(PREFERENCES_PATH, json={"key": "нет_такого_ключа", "value": 1})
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"
    assert await _rows(app_fixture) == []


@pytest.mark.asyncio
async def test_patch_rejects_unknown_enum_option(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(
        PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "ctrl_enter"}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "setting_value_invalid"
    assert body["constraint"]["key"] == CHAT_SEND_KEY
    assert body["constraint"]["enum_values"] == ["enter", "shift_enter"]
    assert "enter" in body["hint"] and "shift_enter" in body["hint"]
    assert await _rows(app_fixture) == []


@pytest.mark.asyncio
async def test_patch_rejects_null_value(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": None})
    assert resp.status_code == 422
    assert await _rows(app_fixture) == []


@pytest.mark.asyncio
async def test_patch_rejects_extra_fields(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(
        PREFERENCES_PATH,
        json={"key": CHAT_SEND_KEY, "value": "enter", "apply_to_all": True},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_empty_key(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(PREFERENCES_PATH, json={"key": "", "value": "enter"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_overlong_key(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Предел длины ключа совпадает с шириной колонки обеих таблиц настроек."""
    await _login(api_client, app_fixture)

    resp = await api_client.patch(PREFERENCES_PATH, json={"key": "k" * 129, "value": "enter"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Изоляция по владельцу
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preferences_are_isolated_between_users(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Чужая настройка не видна и не меняется: отбор по ``user_id``."""
    first_id = await _login(api_client, app_fixture, name="first")
    await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "shift_enter"})

    # Второй пользователь — та же рабочая область, своя сессия.
    second_id = await _login(api_client, app_fixture, name="second")
    entry = await _get_by_key(api_client, CHAT_SEND_KEY)
    assert (entry["value"], entry["source"]) == ("enter", "default")

    await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "enter"})

    rows = {row.user_id: row.value for row in await _rows(app_fixture)}
    assert rows == {first_id: "shift_enter", second_id: "enter"}


@pytest.mark.asyncio
async def test_other_user_cannot_overwrite_foreign_row(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Запись второго пользователя создаёт его строку, а не правит чужую."""
    first_id = await _login(api_client, app_fixture, name="first")
    await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "shift_enter"})
    await _login(api_client, app_fixture, name="second")

    resp = await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "enter"})
    assert resp.status_code == 200

    rows = await _rows(app_fixture)
    assert len(rows) == 2
    foreign = next(row for row in rows if row.user_id == first_id)
    assert foreign.value == "shift_enter"


# ---------------------------------------------------------------------------
# Аудит не пишется
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_writes_no_audit(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    """Личное содержимое в аудит не попадает — ни при первой записи, ни при повторной."""
    await _login(api_client, app_fixture)
    before = await _audit_count(app_fixture)

    await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "shift_enter"})
    await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "enter"})

    assert await _audit_count(app_fixture) == before


@pytest.mark.asyncio
async def test_failed_write_writes_no_audit(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)
    before = await _audit_count(app_fixture)

    await api_client.patch(PREFERENCES_PATH, json={"key": CHAT_SEND_KEY, "value": "ctrl_enter"})

    assert await _audit_count(app_fixture) == before


# ---------------------------------------------------------------------------
# Новая настройка без миграции
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_registry_key_needs_no_migration(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    registry: dict[str, PreferenceSpec],
) -> None:
    """Ключ, которого нет ни в одной миграции, сразу читается и пишется."""
    registry[CHAT_SEND_KEY] = _PROD_SPECS[CHAT_SEND_KEY]
    registry[RUNTIME_KEY] = PreferenceSpec(
        key=RUNTIME_KEY,
        title="Проба нового ключа",
        description="Добавлен в реестр во время теста",
        category="Общие",
        value_model=ProbeValue,
    )
    await _login(api_client, app_fixture)

    entry = await _get_by_key(api_client, RUNTIME_KEY)
    assert (entry["value"], entry["source"]) == (7, "default")

    resp = await api_client.patch(PREFERENCES_PATH, json={"key": RUNTIME_KEY, "value": 42})
    assert resp.status_code == 200
    assert resp.json()["source"] == "db"

    migrations = Path(_migrations_dir())
    texts = [path.read_text(encoding="utf-8") for path in migrations.glob("*.py")]
    assert texts, "миграции не найдены"
    assert not any(RUNTIME_KEY in text for text in texts)


def _migrations_dir() -> str:
    """Каталог миграций: ``app/db/migrations`` не пакет, путь от models.py."""
    return str(Path(app_models.__file__).parent / "migrations" / "versions")
