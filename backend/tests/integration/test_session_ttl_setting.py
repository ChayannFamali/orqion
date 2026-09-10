"""Срок жизни сессии через реестр служебных настроек.

Проверяется поведение, а не наличие записи: смена значения через API меняет
срок **новых** сессий без перезапуска приложения, уже выданные сессии
остаются со своим сроком, а без записи в БД действует прежнее env-значение.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME
from app.config import Settings
from app.db.models import Role, User
from app.db.models import Session as SessionModel
from app.policy.presets import BUILTIN_ROLES
from app.settings.registry import SESSION_TTL_KEY
from fastapi import FastAPI
from sqlalchemy import select

SETTINGS_PATH = "/api/workspace/settings"
LOGIN_PATH = "/api/auth/login"


async def _seed_user(app_fixture: FastAPI, *, role_name: str, email: str) -> tuple[str, str]:
    """Пользователь в рабочей области приложения; возвращает (email, пароль)."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    password = f"ttl-{role_name}-pass-123"
    async with factory() as session:
        role = Role(
            workspace_id=workspace_id,
            name=f"ttl-{role_name}",
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


async def _login(api_client: httpx.AsyncClient, creds: tuple[str, str]) -> str:
    """Вход через HTTP; возвращает id выданной сессии из cookie."""
    api_client.cookies.clear()
    response = await api_client.post(LOGIN_PATH, json={"email": creds[0], "password": creds[1]})
    assert response.status_code == 200, response.text[:300]
    return response.cookies[COOKIE_NAME]


async def _expires_at(app_fixture: FastAPI, session_id: str) -> datetime:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        record = (
            await session.execute(select(SessionModel).where(SessionModel.id == session_id))
        ).scalar_one()
        expires: datetime = record.expires_at
        return expires


def _ttl_days(expires_at: datetime) -> float:
    """Срок сессии в днях от текущего момента."""
    moment = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    return (moment - datetime.now(UTC)).total_seconds() / 86400


async def _patch(api_client: httpx.AsyncClient, value: object) -> httpx.Response:
    return await api_client.patch(SETTINGS_PATH, json={"key": SESSION_TTL_KEY, "value": value})


# ---------------------------------------------------------------------------
# Поведение
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_without_setting_uses_env_default(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    test_settings: Settings,
) -> None:
    """Без записи в БД поведение не меняется: срок из env-конфига."""
    creds = await _seed_user(app_fixture, role_name="admin", email="ttl-admin@orqion.local")

    session_id = await _login(api_client, creds)

    assert _ttl_days(await _expires_at(app_fixture, session_id)) == pytest.approx(
        test_settings.session_ttl_days, abs=0.01
    )


@pytest.mark.asyncio
async def test_setting_changes_ttl_of_new_sessions_without_restart(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    test_settings: Settings,
) -> None:
    """Приёмка задачи: смена значения меняет реальный TTL новых сессий."""
    creds = await _seed_user(app_fixture, role_name="admin", email="ttl-admin2@orqion.local")
    old_session_id = await _login(api_client, creds)
    old_expires = await _expires_at(app_fixture, old_session_id)

    response = await _patch(api_client, 2)
    assert response.status_code == 200, response.text[:300]
    assert response.json()["source"] == "db"

    new_session_id = await _login(api_client, creds)

    assert new_session_id != old_session_id
    assert _ttl_days(await _expires_at(app_fixture, new_session_id)) == pytest.approx(2, abs=0.01)
    # Выданная до смены сессия свой срок сохранила.
    assert await _expires_at(app_fixture, old_session_id) == old_expires
    assert _ttl_days(old_expires) == pytest.approx(test_settings.session_ttl_days, abs=0.01)


@pytest.mark.asyncio
async def test_reverting_to_default_requires_deleting_nothing(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    test_settings: Settings,
) -> None:
    """Запись значения, равного env-дефолту, даёт тот же срок.

    Отдельная проверка потому, что «совпало с дефолтом» — единственный случай,
    где ошибка резолва (запись проигнорирована) была бы не видна по сроку.
    """
    creds = await _seed_user(app_fixture, role_name="admin", email="ttl-admin3@orqion.local")
    await _login(api_client, creds)

    assert (await _patch(api_client, test_settings.session_ttl_days)).status_code == 200
    session_id = await _login(api_client, creds)

    assert _ttl_days(await _expires_at(app_fixture, session_id)) == pytest.approx(
        test_settings.session_ttl_days, abs=0.01
    )


@pytest.mark.asyncio
async def test_user_without_right_cannot_change_ttl(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    test_settings: Settings,
) -> None:
    """Без способности на ключ — 404, и срок сессий остаётся прежним."""
    admin = await _seed_user(app_fixture, role_name="admin", email="ttl-admin4@orqion.local")
    developer = await _seed_user(app_fixture, role_name="developer", email="ttl-dev@orqion.local")

    await _login(api_client, developer)
    response = await _patch(api_client, 2)
    assert response.status_code == 404, response.text[:300]

    # Проверка на том же клиенте под администратором: запись не появилась.
    session_id = await _login(api_client, admin)
    assert _ttl_days(await _expires_at(app_fixture, session_id)) == pytest.approx(
        test_settings.session_ttl_days, abs=0.01
    )


@pytest.mark.asyncio
async def test_invalid_value_rejected_and_ttl_unchanged(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    test_settings: Settings,
) -> None:
    """Границы из описания ключа действуют на реальном пути выдачи сессий."""
    creds = await _seed_user(app_fixture, role_name="admin", email="ttl-admin5@orqion.local")
    await _login(api_client, creds)

    for value in (0, -5, 366, "неделя"):
        response = await _patch(api_client, value)
        assert response.status_code == 422, (value, response.text[:200])

    session_id = await _login(api_client, creds)
    assert _ttl_days(await _expires_at(app_fixture, session_id)) == pytest.approx(
        test_settings.session_ttl_days, abs=0.01
    )


# ---------------------------------------------------------------------------
# Видимость в каталоге (вкладка интерфейса строится из этих данных)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_visible_in_catalog_with_category(
    api_client: httpx.AsyncClient, app_fixture: FastAPI
) -> None:
    """Ключ приходит в каталоге со своей категорией — вкладка появляется сама."""
    creds = await _seed_user(app_fixture, role_name="admin", email="ttl-admin6@orqion.local")
    await _login(api_client, creds)

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    entry = next(item for item in entries if item["key"] == SESSION_TTL_KEY)
    assert entry["category"] == "Сессии и безопасность"
    assert entry["title"] == "Срок жизни сессии (дней)"
    assert (entry["type"], entry["min"], entry["max"]) == ("integer", 1.0, 365.0)
    assert entry["source"] == "default"
    assert entry["editable"] is True

    await _patch(api_client, 14)
    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    entry = next(item for item in entries if item["key"] == SESSION_TTL_KEY)
    assert (entry["value"], entry["source"]) == (14, "db")


@pytest.mark.asyncio
async def test_key_read_only_for_role_without_right(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    test_settings: Settings,
) -> None:
    """Без способности поле только для чтения, но значение видно."""
    developer = await _seed_user(app_fixture, role_name="developer", email="ttl-dev2@orqion.local")
    await _login(api_client, developer)

    entries = (await api_client.get(SETTINGS_PATH)).json()["settings"]
    entry = next(item for item in entries if item["key"] == SESSION_TTL_KEY)
    assert entry["editable"] is False
    assert entry["value"] == test_settings.session_ttl_days
    assert entry["source"] == "default"
