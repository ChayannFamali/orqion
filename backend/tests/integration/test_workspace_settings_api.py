"""API реестра служебных настроек рабочей области.

GET /api/workspace/settings — чтение всем аутентифицированным: все
зарегистрированные ключи с резолвнутым значением (БД или env-дефолт) и
меткой источника.

PATCH /api/workspace/settings — один ключ за вызов: значение проверяется
pydantic-моделью спеки (422), право — способностью именно этого ключа
(404 без права, не общий гейт на маршрут), на каждую успешную запись
пишется аудит ``settings.changed``.

Прод-реестр пуст, поэтому ключи регистрирует фикстура: это одновременно
проверка того, что новая настройка не требует миграции.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db import models as app_models
from app.db.models import AuditLog, Role, User, WorkspaceSetting
from app.policy.models import Policy
from app.policy.presets import BUILTIN_ROLES
from app.settings.registry import SETTINGS_REGISTRY, SettingSpec
from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import select

SETTINGS_PATH = "/api/workspace/settings"

# Имя ключа, которого нет ни в одной миграции: им проверяется, что новая
# настройка появляется одним описанием в реестре.
RUNTIME_KEY = "runtime_only_probe"


class TtlValue(BaseModel):
    value: int = Field(ge=1, le=365)


class UploadLimitValue(BaseModel):
    value: int = Field(default=50, ge=1, le=2048)


class NoteValue(BaseModel):
    value: str = Field(default="", max_length=200)


class ModeValue(BaseModel):
    value: Literal["fast", "balanced", "thorough"] = "balanced"


class FlagValue(BaseModel):
    value: bool = False


class RuntimeValue(BaseModel):
    value: int = 7


def _spec(
    key: str,
    title: str,
    category: str,
    value_model: type[BaseModel],
    **kwargs: Any,
) -> SettingSpec:
    return SettingSpec(
        key=key,
        title=title,
        description=f"Описание настройки {title.lower()}",
        category=category,
        value_model=value_model,
        **kwargs,
    )


_SPECS: dict[str, SettingSpec] = {
    "session_ttl_days": _spec(
        "session_ttl_days",
        "Срок жизни сессии (дней)",
        "Сессии и безопасность",
        TtlValue,
        default_from_env="session_ttl_days",
    ),
    "upload_limit_mb": _spec(
        "upload_limit_mb",
        "Предел размера файла (МБ)",
        "Файлы и хранение",
        UploadLimitValue,
    ),
    "generation_mode": _spec(
        "generation_mode",
        "Режим генерации",
        "Модель по умолчанию",
        ModeValue,
    ),
    "compact_sidebar": _spec(
        "compact_sidebar",
        "Компактный список разделов",
        "Общие",
        FlagValue,
    ),
    # Ключ с другой способностью: доказывает, что право проверяется по тегу
    # ключа, а не общим гейтом на маршрут.
    "public_note": _spec(
        "public_note",
        "Заметка на странице входа",
        "Общие",
        NoteValue,
        write_capability="manage_corpora",
    ),
}


@pytest.fixture(autouse=True)
def _registry() -> Any:
    """Подставляет тестовые ключи и возвращает реестр прежним."""
    saved = dict(SETTINGS_REGISTRY)
    SETTINGS_REGISTRY.clear()
    SETTINGS_REGISTRY.update(_SPECS)
    yield SETTINGS_REGISTRY
    SETTINGS_REGISTRY.clear()
    SETTINGS_REGISTRY.update(saved)


# Настоящие настройки для создания сессии: фикстура autouse заполняет
# держатель до тела теста.
_ACTIVE_SETTINGS: list[Settings] = []


@pytest.fixture(autouse=True)
def _capture_test_settings(test_settings: Settings) -> None:
    _ACTIVE_SETTINGS.clear()
    _ACTIVE_SETTINGS.append(test_settings)


async def _login(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    *,
    role_name: str = "admin",
    capabilities: list[str] | None = None,
) -> str:
    """Сессия пользователя; возвращает его id.

    ``capabilities=None`` — посевной пресет без изменений.
    """
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    settings: Settings = _ACTIVE_SETTINGS[0]
    async with factory() as session:
        if capabilities is None:
            role_policy = BUILTIN_ROLES[role_name].model_dump()
        else:
            role_policy = Policy(
                models=["*"], corpora=["*"], capabilities=capabilities
            ).model_dump()
        role = Role(
            workspace_id=workspace_id,
            name=f"ws-settings-{role_name}",
            is_builtin=capabilities is None,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email=f"ws-settings-{role_name}@orqion.local",
            password_hash=hash_password("settings-pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, settings)
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _rows(app_fixture: FastAPI) -> list[WorkspaceSetting]:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        result = await session.execute(
            select(WorkspaceSetting).where(WorkspaceSetting.workspace_id == workspace_id)
        )
        return list(result.scalars().all())


async def _audit(app_fixture: FastAPI) -> list[AuditLog]:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "settings.changed")
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_requires_auth(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get(SETTINGS_PATH)).status_code == 401


@pytest.mark.asyncio
async def test_patch_requires_auth(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 10})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_lists_registered_keys_with_defaults(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.get(SETTINGS_PATH)
    assert resp.status_code == 200
    entries = resp.json()["settings"]
    assert {entry["key"] for entry in entries} == set(_SPECS)
    assert all(entry["source"] == "default" for entry in entries)
    assert all(entry["editable"] is True for entry in entries)

    by_key = {entry["key"]: entry for entry in entries}
    assert by_key["upload_limit_mb"]["value"] == 50
    assert by_key["generation_mode"]["value"] == "balanced"
    assert by_key["compact_sidebar"]["value"] is False
    assert by_key["public_note"]["value"] == ""


@pytest.mark.asyncio
async def test_get_is_sorted_by_category_then_key(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    pairs = [(entry["category"], entry["key"]) for entry in entries]
    assert pairs == sorted(pairs)
    assert len(pairs) == len(set(pairs))


@pytest.mark.asyncio
async def test_get_resolves_default_from_env_config(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """До первой записи значение наследуется из env-конфига прозрачно."""
    monkeypatch.setenv("ORQION_SESSION_TTL_DAYS", "21")
    await _login(api_client, app_fixture)

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    entry = next(item for item in entries if item["key"] == "session_ttl_days")
    assert entry["value"] == 21
    assert entry["source"] == "default"


@pytest.mark.asyncio
async def test_get_describes_field_for_ui(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """По каждому ключу отдано всё, что нужно нарисовать поле без знания ключа."""
    await _login(api_client, app_fixture)

    by_key = {
        entry["key"]: entry for entry in (await api_client.get(SETTINGS_PATH)).json()["settings"]
    }
    assert by_key["session_ttl_days"]["type"] == "integer"
    assert (by_key["session_ttl_days"]["min"], by_key["session_ttl_days"]["max"]) == (1.0, 365.0)
    assert by_key["session_ttl_days"]["enum_values"] is None
    assert by_key["generation_mode"]["type"] == "enum"
    assert by_key["generation_mode"]["enum_values"] == ["fast", "balanced", "thorough"]
    assert by_key["compact_sidebar"]["type"] == "boolean"
    assert by_key["public_note"]["type"] == "string"
    assert by_key["public_note"]["title"] == "Заметка на странице входа"
    assert by_key["public_note"]["category"] == "Общие"
    assert by_key["public_note"]["description"]


@pytest.mark.asyncio
async def test_get_readable_without_any_write_right(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Чтение доступно всем аутентифицированным, включая роль без прав."""
    await _login(api_client, app_fixture, role_name="support")

    resp = await api_client.get(SETTINGS_PATH)
    assert resp.status_code == 200
    assert {entry["key"] for entry in resp.json()["settings"]} == set(_SPECS)
    assert all(entry["editable"] is False for entry in resp.json()["settings"])


@pytest.mark.asyncio
async def test_editable_follows_capability_of_each_key(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Право мельче маршрута: роль с manage_corpora видит активным один ключ."""
    await _login(api_client, app_fixture, role_name="architect")

    by_key = {
        entry["key"]: entry for entry in (await api_client.get(SETTINGS_PATH)).json()["settings"]
    }
    assert by_key["public_note"]["editable"] is True
    assert by_key["session_ttl_days"]["editable"] is False
    assert by_key["upload_limit_mb"]["editable"] is False


# ---------------------------------------------------------------------------
# PATCH — запись
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_writes_value_and_marks_source_db(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 128})
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "upload_limit_mb"
    assert body["value"] == 128
    assert body["source"] == "db"

    rows = await _rows(app_fixture)
    assert len(rows) == 1
    assert rows[0].value == 128

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    entry = next(item for item in entries if item["key"] == "upload_limit_mb")
    assert (entry["value"], entry["source"]) == (128, "db")


@pytest.mark.asyncio
async def test_patch_records_actor_in_row(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    user_id = await _login(api_client, app_fixture)

    await api_client.patch(SETTINGS_PATH, json={"key": "compact_sidebar", "value": True})

    rows = await _rows(app_fixture)
    assert rows[0].updated_by == user_id
    assert rows[0].updated_at is not None


@pytest.mark.asyncio
async def test_patch_keeps_single_row_per_key(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 64})
    await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 256})

    rows = await _rows(app_fixture)
    assert len(rows) == 1
    assert rows[0].value == 256


@pytest.mark.asyncio
async def test_patch_accepts_every_value_type(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    cases = [
        ("upload_limit_mb", 10, 10),
        ("generation_mode", "thorough", "thorough"),
        ("compact_sidebar", True, True),
        ("public_note", "Технические работы", "Технические работы"),
    ]
    for key, value, expected in cases:
        resp = await api_client.patch(SETTINGS_PATH, json={"key": key, "value": value})
        assert resp.status_code == 200, key
        assert resp.json()["value"] == expected, key


# ---------------------------------------------------------------------------
# PATCH — валидация по спеке (422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_rejects_out_of_range_value(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 4096})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "setting_value_invalid"
    assert body["constraint"]["key"] == "upload_limit_mb"
    assert "от 1 до 2048" in body["hint"]
    assert await _rows(app_fixture) == []


@pytest.mark.asyncio
async def test_patch_rejects_wrong_type(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": "много"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "setting_value_invalid"
    assert "целое число" in resp.json()["hint"]


@pytest.mark.asyncio
async def test_patch_rejects_unknown_enum_option(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "generation_mode", "value": "turbo"})
    assert resp.status_code == 422
    hint = resp.json()["hint"]
    assert "fast" in hint and "thorough" in hint


@pytest.mark.asyncio
async def test_patch_rejects_null_for_typed_value(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": None})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_extra_fields(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(
        SETTINGS_PATH,
        json={"key": "upload_limit_mb", "value": 10, "apply_to_all": True},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_long_string_value(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "public_note", "value": "а" * 201})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH — доступ по тегу ключа (404)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_unknown_key_404(api_client: httpx.AsyncClient, app_fixture: FastAPI) -> None:
    await _login(api_client, app_fixture)

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "нет_такого_ключа", "value": 1})
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_patch_without_capability_404(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture, role_name="developer")

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 10})
    assert resp.status_code == 404
    assert await _rows(app_fixture) == []
    assert await _audit(app_fixture) == []


@pytest.mark.asyncio
async def test_patch_key_with_own_capability_allowed(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Один и тот же маршрут: ключ со своей способностью записывается."""
    await _login(api_client, app_fixture, role_name="architect")

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "public_note", "value": "Обед"})
    assert resp.status_code == 200
    assert (await _rows(app_fixture))[0].value == "Обед"


@pytest.mark.asyncio
async def test_patch_other_key_still_404_for_same_user(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Тот же пользователь, соседний ключ — 404: гейт не общий на маршрут."""
    await _login(api_client, app_fixture, role_name="architect")

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 10})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_capability_granted_explicitly_allows_write(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture, role_name="custom", capabilities=["manage_settings"])

    resp = await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 10})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Аудит
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_writes_audit_with_old_and_new(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    user_id = await _login(api_client, app_fixture)
    workspace_id = app_fixture.state.workspace_id

    await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 128})

    records = await _audit(app_fixture)
    assert len(records) == 1
    record = records[0]
    assert record.actor_user_id == user_id
    assert record.object_type == "workspace_setting"
    assert record.object_id == workspace_id
    assert record.meta == {"key": "upload_limit_mb", "old": 50, "new": 128}


@pytest.mark.asyncio
async def test_audit_old_value_is_env_default_before_first_write(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Старое значение первой записи — дефолт, который видел пользователь."""
    monkeypatch.setenv("ORQION_SESSION_TTL_DAYS", "3")
    await _login(api_client, app_fixture)

    await api_client.patch(SETTINGS_PATH, json={"key": "session_ttl_days", "value": 10})

    records = await _audit(app_fixture)
    assert records[0].meta == {"key": "session_ttl_days", "old": 3, "new": 10}


@pytest.mark.asyncio
async def test_audit_records_previous_db_value(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    await api_client.patch(SETTINGS_PATH, json={"key": "generation_mode", "value": "fast"})
    await api_client.patch(SETTINGS_PATH, json={"key": "generation_mode", "value": "thorough"})

    records = await _audit(app_fixture)
    assert [record.meta for record in records] == [
        {"key": "generation_mode", "old": "balanced", "new": "fast"},
        {"key": "generation_mode", "old": "fast", "new": "thorough"},
    ]


@pytest.mark.asyncio
async def test_repeated_write_of_same_value_still_audited(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Аудит задан на каждую успешную запись: повтор тоже факт обращения."""
    await _login(api_client, app_fixture)

    await api_client.patch(SETTINGS_PATH, json={"key": "compact_sidebar", "value": True})
    await api_client.patch(SETTINGS_PATH, json={"key": "compact_sidebar", "value": True})

    records = await _audit(app_fixture)
    assert [record.meta for record in records] == [
        {"key": "compact_sidebar", "old": False, "new": True},
        {"key": "compact_sidebar", "old": True, "new": True},
    ]


@pytest.mark.asyncio
async def test_failed_write_leaves_no_audit(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    await _login(api_client, app_fixture)

    await api_client.patch(SETTINGS_PATH, json={"key": "upload_limit_mb", "value": 0})

    assert await _audit(app_fixture) == []


# ---------------------------------------------------------------------------
# Новая настройка без миграции
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_registry_key_needs_no_migration(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Ключ, которого нет ни в одной миграции, сразу читается и пишется."""
    SETTINGS_REGISTRY[RUNTIME_KEY] = SettingSpec(
        key=RUNTIME_KEY,
        title="Проба нового ключа",
        description="Добавлен в реестр во время теста",
        category="Общие",
        value_model=RuntimeValue,
    )
    await _login(api_client, app_fixture)

    listed = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    entry = next(item for item in listed if item["key"] == RUNTIME_KEY)
    assert (entry["value"], entry["source"]) == (7, "default")

    resp = await api_client.patch(SETTINGS_PATH, json={"key": RUNTIME_KEY, "value": 42})
    assert resp.status_code == 200
    assert resp.json()["source"] == "db"

    migrations = Path(_migrations_dir())
    texts = [path.read_text(encoding="utf-8") for path in migrations.glob("*.py")]
    assert texts, "миграции не найдены"
    assert not any(RUNTIME_KEY in text for text in texts)


def _migrations_dir() -> str:
    """Каталог миграций: ``app/db/migrations`` не пакет, путь от models.py."""
    return str(Path(app_models.__file__).parent / "migrations" / "versions")
