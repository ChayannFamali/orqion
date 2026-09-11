"""Т-509: реестр профилей агентов — API, решения мини-дизайн-ревью 1–9.

Покрытие по решениям:

- решение 1 — профиль это модель + скилл: полей промпта/инструментов в
  схеме нет (``extra="forbid"`` → 422), ``supports_tools=false`` на
  создании → 400, профиль без скилла создаётся;
- решение 2 — профиль фиксирует модель и скилл диалога: переопределение в
  запросе → 400 ``agent_profile_conflict``, продолжение диалога идёт с
  моделью профиля даже без ``model_alias`` в запросе;
- решение 3 — доступ: без ``manage_agents`` → 404 на каталог и записи,
  ``/available`` доступен всем аутентифицированным и отдаёт только
  включённые и только поля выбора;
- решение 4 — ``agent_profile_id`` на диалоге записан, ad-hoc диалог его
  не имеет;
- решение 5 — drill-down: либо только свои диалоги, либо все-но-метаданные;
  содержимое переписки не отдаётся никогда;
- решение 6 — архивированный диалог отвечает явным 400 на новое сообщение;
- решение 7 — остановка: ``POST /api/conversations/{id}/stop`` ставит флаг
  и пишет ``agent.conversation.stop_requested``, прогон завершается между
  шагами с ``type="stopped"`` и фактом ``agent.conversation.stopped``;
- решение 8 — удаление профиля с диалогами → 409, без диалогов → 200;
- решение 9 — ad-hoc путь сохраняется: диалог без профиля продолжает
  работать ручным выбором модели.

Провайдер подменяется заглушкой — обращения к сети запрещены.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.api.schemas.agent_profiles import NAME_MAX_LENGTH
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import (
    AgentProfile,
    AuditLog,
    Conversation,
    Model,
    Provider,
    Role,
    UsageEvent,
    User,
)
from app.policy.models import Policy
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from fastapi import FastAPI
from sqlalchemy import select

PROFILES_PATH = "/api/agent-profiles"
AVAILABLE_PATH = "/api/agent-profiles/available"
AGENT_PATH = "/api/agent/chat"

_MODEL_A = "local/profile-a"
_MODEL_B = "local/profile-b"
# Имя, с которым модель уходит провайдеру: отдельное у каждой, иначе журнал
# вызовов заглушки не доказывает, что прогон шёл именно с моделью профиля
# (решение 2), — обе модели выглядели бы одинаково.
_UPSTREAM_A = "upstream-profile-a"
_UPSTREAM_B = "upstream-profile-b"

# Настоящие настройки для создания сессии: ``_login`` берёт их отсюда,
# потому что часть тестов подменяет ``Settings.__init__`` заглушкой (тогда
# свежий экземпляр настроек пуст). Фикстура autouse заполняет держатель до
# тела теста — порядок фикстур pytest это гарантирует.
_ACTIVE_SETTINGS: list[Settings] = []


@pytest.fixture(autouse=True)
def _capture_test_settings(test_settings: Settings) -> None:
    _ACTIVE_SETTINGS.clear()
    _ACTIVE_SETTINGS.append(test_settings)


async def _login(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
    *,
    email_suffix: str,
    capabilities: list[str] | None = None,
) -> str:
    """Сессия пользователя с заданной политикой.

    ``capabilities=None`` — посевной пресет без изменений: это и есть
    доказательство того, что ``manage_agents`` не попал ни в один пресет и
    выдаётся только вместе с ``*`` (решение 3, паттерн ``manage_skills``).

    Подмену настроек тесты не делают вовсе: настройки берутся готовыми из
    состояния приложения (``EnvFreeSettings`` — без чтения окружения), а
    лимиты при необходимости задаются через переменные окружения
    (``monkeypatch.setenv``), как в тестах скиллов Т-508.
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
            name=f"agent-profiles-{email_suffix}",
            is_builtin=capabilities is None,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email=f"agent-profiles-{email_suffix}@orqion.local",
            password_hash=hash_password("profile-pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()
        session_id = await create_session(session, user.id, workspace_id, settings)
        await session.commit()
    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_models(
    app_fixture: FastAPI,
    *,
    aliases: tuple[str, ...] = (_MODEL_A, _MODEL_B),
    supports_tools: bool = True,
) -> dict[str, str]:
    """Провайдер и модели для профилей; возвращает alias → model_id."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind="openai",
            base_url="http://stub:1234/v1",
            api_key_enc=encrypt_api_key("sk-test", app_fixture.state.secret_key),
            enabled=True,
            capabilities={},
        )
        session.add(provider)
        await session.flush()
        ids: dict[str, str] = {}
        for alias in aliases:
            model = Model(
                workspace_id=workspace_id,
                provider_id=provider.id,
                alias=alias,
                upstream_name=_UPSTREAM_B if alias == _MODEL_B else _UPSTREAM_A,
                locality="local",
                max_input_tokens=32000,
                enabled=True,
                supports_tools=supports_tools,
            )
            session.add(model)
            await session.flush()
            ids[alias] = model.id
        await session.commit()
        return ids


async def _seed_profile(
    app_fixture: FastAPI,
    model_id: str,
    *,
    name: str = "seeded-profile",
    enabled: bool = True,
    skill_id: str | None = None,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        profile = AgentProfile(
            workspace_id=workspace_id,
            name=name,
            description="посев",
            model_id=model_id,
            skill_id=skill_id,
            enabled=enabled,
        )
        session.add(profile)
        await session.commit()
        return profile.id


async def _seed_skill(app_fixture: FastAPI, *, name: str = "seeded-skill") -> str:
    from app.db.models import AgentSkill

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        row = AgentSkill(
            workspace_id=workspace_id,
            name=name,
            description="посевной скилл",
            prompt_text="Ты — ассистент поддержки.",
            tools=["search_corpus"],
            enabled=True,
        )
        session.add(row)
        await session.commit()
        return row.id


def _patch_model(
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str = "Готово по профилю",
) -> dict[str, list[str]]:
    """Провайдер без сети; записывает, с какой моделью его вызвали.

    Запись имени модели — факт для проверки того, что прогон шёл именно с
    моделью профиля (решение 2), а не с той, что клиент положил в запрос.
    """
    calls: dict[str, list[str]] = {"models": []}

    async def _stub(
        self: ProviderClient,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        calls["models"].append(model)
        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    monkeypatch.setattr(ProviderClient, "complete_tools", _stub)
    return calls


async def _agent_run(
    api_client: httpx.AsyncClient,
    *,
    agent_profile_id: str | None = None,
    model_alias: str | None = None,
    conversation_id: str | None = None,
    content: str = "Собери отчёт",
) -> httpx.Response:
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": content}],
    }
    if agent_profile_id is not None:
        body["agent_profile_id"] = agent_profile_id
    if model_alias is not None:
        body["model_alias"] = model_alias
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return await api_client.post(AGENT_PATH, json=body)


# ---------------------------------------------------------------------------
# решение 1: состав профиля и валидация модели
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_profile_persists_model_and_skill(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin", email_suffix="admin-create")
    ids = await _seed_models(app_fixture)
    skill_id = await _seed_skill(app_fixture)

    resp = await api_client.post(
        PROFILES_PATH,
        json={
            "name": "Аналитик",
            "description": "отчёты по корпусу",
            "model_id": ids[_MODEL_A],
            "skill_id": skill_id,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Аналитик"
    assert body["model_id"] == ids[_MODEL_A]
    assert body["model_alias"] == _MODEL_A
    assert body["skill_id"] == skill_id
    assert body["skill_name"] == "seeded-skill"
    assert body["enabled"] is True

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        row = await session.get(AgentProfile, body["id"])
        assert row is not None
        assert row.model_id == ids[_MODEL_A]
        assert row.skill_id == skill_id
        assert row.enabled is True

        audits = (await session.execute(select(AuditLog))).scalars().all()
    profile_audits = [a for a in audits if a.action == "agent_profile.changed"]
    assert len(profile_audits) == 1
    assert profile_audits[0].meta is not None
    assert profile_audits[0].meta["old"] is None
    assert profile_audits[0].meta["new"]["model_id"] == ids[_MODEL_A]
    assert profile_audits[0].meta["new"]["skill_id"] == skill_id


@pytest.mark.asyncio
async def test_create_profile_without_skill_allowed(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 1: профиль без скилла — агент без сужения (не ошибка)."""
    await _login(api_client, app_fixture, "admin", email_suffix="admin-noskill")
    ids = await _seed_models(app_fixture)

    resp = await api_client.post(
        PROFILES_PATH,
        json={"name": "Без скилла", "model_id": ids[_MODEL_A]},
    )
    assert resp.status_code == 201
    assert resp.json()["skill_id"] is None
    assert resp.json()["skill_name"] is None


@pytest.mark.asyncio
async def test_create_profile_rejects_model_without_tool_support(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 1: ``supports_tools=false`` на создании — 400, не запись."""
    await _login(api_client, app_fixture, "admin", email_suffix="admin-notools")
    ids = await _seed_models(app_fixture, aliases=("local/no-tools",), supports_tools=False)

    resp = await api_client.post(
        PROFILES_PATH,
        json={"name": "Непрофиль", "model_id": ids["local/no-tools"]},
    )
    assert resp.status_code == 400
    assert resp.json()["constraint"]["model_id"] == ids["local/no-tools"]

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        count = len((await session.execute(select(AgentProfile))).scalars().all())
    assert count == 0


@pytest.mark.asyncio
async def test_create_profile_rejects_unknown_fields(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 1: промпта и списка инструментов в профиле нет построением.

    ``extra="forbid"``: попытка передать ``system_prompt``, ``tools`` или
    поле «доверенности» (``auto_approve``) — 422, а не молчаливое
    игнорирование. Иначе вторая сущность «промпт + инструменты» появилась
    бы обходным путём — через запрос.
    """
    await _login(api_client, app_fixture, "admin", email_suffix="admin-extra")
    ids = await _seed_models(app_fixture)

    for extra in (
        {"system_prompt": "будь добрым"},
        {"tools": ["search_corpus"]},
        {"auto_approve": True},
    ):
        resp = await api_client.post(
            PROFILES_PATH,
            json={"name": "С лишним", "model_id": ids[_MODEL_A], **extra},
        )
        assert resp.status_code == 422, extra
        assert "Extra inputs are not permitted" in resp.text


@pytest.mark.asyncio
async def test_create_profile_rejects_missing_or_unknown_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin", email_suffix="admin-nomodel")
    await _seed_models(app_fixture)

    missing = await api_client.post(PROFILES_PATH, json={"name": "Без модели", "model_id": "ghost"})
    assert missing.status_code == 404

    # Пустая строка — тоже несуществующая модель: отказ тот же, 404.
    empty = await api_client.post(PROFILES_PATH, json={"name": "Пустая модель", "model_id": ""})
    assert empty.status_code == 404

    no_field = await api_client.post(PROFILES_PATH, json={"name": "Без поля"})
    assert no_field.status_code == 422


@pytest.mark.asyncio
async def test_create_profile_rejects_name_too_long(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin", email_suffix="admin-longname")
    ids = await _seed_models(app_fixture)
    resp = await api_client.post(
        PROFILES_PATH,
        json={"name": "п" * (NAME_MAX_LENGTH + 1), "model_id": ids[_MODEL_A]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_profile_duplicate_name_conflict(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin", email_suffix="admin-dupname")
    ids = await _seed_models(app_fixture)
    first = await api_client.post(
        PROFILES_PATH, json={"name": "Двойник", "model_id": ids[_MODEL_A]}
    )
    assert first.status_code == 201
    second = await api_client.post(
        PROFILES_PATH, json={"name": "Двойник", "model_id": ids[_MODEL_B]}
    )
    assert second.status_code == 409
    assert second.json()["constraint"]["name"] == "Двойник"


# ---------------------------------------------------------------------------
# решение 3: доступ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manage_agents_is_wildcard_only(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Посевные пресеты права не содержат; admin имеет его через ``*``."""
    assert "manage_agents" not in BUILTIN_ROLES["admin"].capabilities
    assert "*" in BUILTIN_ROLES["admin"].capabilities
    # Все пять встроенных ролей (arch.md §3): право не попало ни в одну.
    assert set(BUILTIN_ROLES) == {"support", "developer", "architect", "manager", "admin"}
    for name in ("support", "developer", "architect", "manager"):
        assert "manage_agents" not in BUILTIN_ROLES[name].capabilities

    await _login(api_client, app_fixture, "admin", email_suffix="admin-wildcard")
    ids = await _seed_models(app_fixture)
    created = await api_client.post(
        PROFILES_PATH, json={"name": "По вайлдкарду", "model_id": ids[_MODEL_A]}
    )
    assert created.status_code == 201


@pytest.mark.asyncio
async def test_explicit_manage_agents_capability_grants_access(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login(
        api_client,
        app_fixture,
        "developer",
        email_suffix="dev-explicit",
        capabilities=["manage_agents"],
    )
    ids = await _seed_models(app_fixture)
    created = await api_client.post(
        PROFILES_PATH, json={"name": "Явное право", "model_id": ids[_MODEL_A]}
    )
    assert created.status_code == 201
    assert (await api_client.get(PROFILES_PATH)).status_code == 200


@pytest.mark.asyncio
async def test_without_manage_agents_404_on_catalog_and_writes(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 3: 404 на каталог и на все пишущие эндпоинты."""
    await _login(api_client, app_fixture, "developer", email_suffix="dev-404")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A])

    assert (await api_client.get(PROFILES_PATH)).status_code == 404
    assert (
        await api_client.post(PROFILES_PATH, json={"name": "Чужой", "model_id": ids[_MODEL_A]})
    ).status_code == 404
    assert (
        await api_client.put(
            f"{PROFILES_PATH}/{profile_id}",
            json={"name": "Переписанный", "model_id": ids[_MODEL_A]},
        )
    ).status_code == 404
    assert (await api_client.delete(f"{PROFILES_PATH}/{profile_id}")).status_code == 404

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        row = await session.get(AgentProfile, profile_id)
        assert row is not None
        assert row.name == "seeded-profile"


@pytest.mark.asyncio
async def test_available_list_open_to_authenticated_and_hides_disabled(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 3: список выбора — всем аутентифицированным, только включённые.

    Отключённый профиль не должен появляться в списке выбора, иначе
    прогон от него закончится 400 ``agent_profile_not_available`` —
    предложение, которое заведомо не работает.
    """
    await _login(api_client, app_fixture, "developer", email_suffix="dev-available")
    ids = await _seed_models(app_fixture)
    enabled_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Рабочий")
    await _seed_profile(app_fixture, ids[_MODEL_B], name="Отключённый", enabled=False)

    resp = await api_client.get(AVAILABLE_PATH)
    assert resp.status_code == 200
    entries = resp.json()["profiles"]
    assert [entry["name"] for entry in entries] == ["Рабочий"]
    assert entries[0]["id"] == enabled_id
    # Только поля выбора: модель и скилл не раскрываются без права.
    assert set(entries[0]) == {"id", "name", "description"}


@pytest.mark.asyncio
async def test_available_list_requires_authentication(
    api_client: httpx.AsyncClient,
) -> None:
    resp = await api_client.get(AVAILABLE_PATH)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# решение 2 и 4: профиль фиксирует модель и скилл диалога
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_from_profile_uses_profile_model_and_pins_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Прогон от профиля: модель — из профиля, диалог помнит профиль."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-run")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_B], name="Прогонный")
    calls = _patch_model(monkeypatch)

    resp = await _agent_run(api_client, agent_profile_id=profile_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["type"] == "complete"
    assert body["content"] == "Готово по профилю"
    assert body["model"] == _MODEL_B
    # Провайдеру уходит upstream_name модели, а не alias.
    assert calls["models"] == [_UPSTREAM_B]

    conversation_id = body["conversation_id"]
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.mode == "agent"
        assert conversation.agent_profile_id == profile_id
        assert conversation.stop_requested is False


@pytest.mark.asyncio
async def test_profile_conversation_continues_without_model_in_request(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Продолжение диалога: профиль берётся с диалога, не из запроса."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-continue")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_B], name="Продолжаемый")
    calls = _patch_model(monkeypatch)

    first = await _agent_run(api_client, agent_profile_id=profile_id)
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    second = await _agent_run(api_client, conversation_id=conversation_id)
    assert second.status_code == 200
    assert second.json()["model"] == _MODEL_B
    assert calls["models"] == [_UPSTREAM_B, _UPSTREAM_B]

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.agent_profile_id == profile_id


@pytest.mark.asyncio
async def test_profile_cannot_be_overridden_in_request(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 2: переопределение модели/скилла в запросе — явный 400."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-override")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Непереопределяемый")
    calls = _patch_model(monkeypatch)

    with_model = await _agent_run(api_client, agent_profile_id=profile_id, model_alias=_MODEL_B)
    assert with_model.status_code == 400
    assert with_model.json()["error"] == "agent_profile_conflict"

    conflict_skill = await api_client.post(
        AGENT_PATH,
        json={
            "messages": [{"role": "user", "content": "вопрос"}],
            "agent_profile_id": profile_id,
            "skill_id": "ghost",
        },
    )
    assert conflict_skill.status_code == 400
    assert conflict_skill.json()["error"] == "agent_profile_conflict"

    # Конфликт отклоняется до обращения к провайдеру: ни одного вызова.
    assert calls["models"] == []

    # Профиль без переопределений работает штатно.
    ok = await _agent_run(api_client, agent_profile_id=profile_id)
    assert ok.status_code == 200
    assert ok.json()["model"] == _MODEL_A
    assert calls["models"] == [_UPSTREAM_A]


@pytest.mark.asyncio
async def test_run_from_profile_of_pinned_conversation_rejects_other_model(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Диалог от профиля не отпускается на другую модель даже прямым запросом."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-pinned")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Закреплённый")
    _patch_model(monkeypatch)

    first = await _agent_run(api_client, agent_profile_id=profile_id)
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    resp = await _agent_run(api_client, conversation_id=conversation_id, model_alias=_MODEL_B)
    assert resp.status_code == 400
    assert resp.json()["error"] == "agent_profile_conflict"
    assert resp.json()["constraint"]["agent_profile_id"] == profile_id


@pytest.mark.asyncio
async def test_run_from_unknown_or_disabled_profile_fails_closed(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed по образцу скиллов: призрак и отключённый профиль — 400."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-failclosed")
    ids = await _seed_models(app_fixture)
    disabled_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Выключенный", enabled=False)
    calls = _patch_model(monkeypatch)

    ghost = await _agent_run(api_client, agent_profile_id="ghost-profile")
    assert ghost.status_code == 400
    assert ghost.json()["error"] == "agent_profile_not_available"

    disabled = await _agent_run(api_client, agent_profile_id=disabled_id)
    assert disabled.status_code == 400
    assert disabled.json()["error"] == "agent_profile_not_available"
    assert calls["models"] == []

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        assert len((await session.execute(select(Conversation))).scalars().all()) == 0


@pytest.mark.asyncio
async def test_profile_run_bills_usage_events(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Биллинг профиля тот же, что у ad-hoc прогона (пункт 8 Т-502)."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-billing")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Платный")
    _patch_model(monkeypatch)

    resp = await _agent_run(api_client, agent_profile_id=profile_id)
    assert resp.status_code == 200
    assert resp.json()["usage"] == {"tokens_in": 3, "tokens_out": 2}

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        events = (await session.execute(select(UsageEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].model_id == ids[_MODEL_A]
    assert events[0].status == "ok"
    assert events[0].conversation_id == resp.json()["conversation_id"]


@pytest.mark.asyncio
async def test_request_without_model_profile_or_conversation_is_422(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Конфигурация прогона обязана быть задана явно (схема, не маршрут)."""
    await _login(api_client, app_fixture, "developer", email_suffix="dev-422")
    await _seed_models(app_fixture)
    resp = await api_client.post(
        AGENT_PATH, json={"messages": [{"role": "user", "content": "вопрос"}]}
    )
    assert resp.status_code == 422
    assert "Value error" in resp.text


# ---------------------------------------------------------------------------
# решение 6: архивированный диалог не принимает сообщения
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archived_conversation_rejects_new_message_explicitly(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 6: явный 400, а не тихая блокировка."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-archived")
    await _seed_models(app_fixture)
    _patch_model(monkeypatch)

    first = await _agent_run(api_client, model_alias=_MODEL_A)
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    archived = await api_client.patch(
        f"/api/conversations/{conversation_id}", json={"archived": True}
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True

    resp = await _agent_run(api_client, conversation_id=conversation_id)
    assert resp.status_code == 400
    assert resp.json()["error"] == "conversation_archived"


# ---------------------------------------------------------------------------
# решение 5: drill-down по диалогам профиля
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drilldown_without_right_returns_only_own_conversations(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("langgraph")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Общий")
    _patch_model(monkeypatch)

    # Сессия А: свой диалог от профиля.
    await _login(api_client, app_fixture, "developer", email_suffix="dev-a")
    own = await _agent_run(api_client, agent_profile_id=profile_id)
    assert own.status_code == 200
    own_conversation = own.json()["conversation_id"]

    # Сессия Б: свой диалог от того же профиля (другой клиент).
    api_client.cookies.clear()
    await _login(api_client, app_fixture, "developer", email_suffix="dev-b")
    other = await _agent_run(api_client, agent_profile_id=profile_id)
    assert other.status_code == 200
    other_conversation = other.json()["conversation_id"]
    assert other_conversation != own_conversation

    resp = await api_client.get(f"{PROFILES_PATH}/{profile_id}/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "own"
    assert body["total"] == 1
    assert [entry["id"] for entry in body["conversations"]] == [other_conversation]
    assert body["conversations"][0]["title"] is not None


@pytest.mark.asyncio
async def test_drilldown_with_right_returns_workspace_metadata_only(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 5: с правом — все диалоги, но содержимого переписки нет."""
    pytest.importorskip("langgraph")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Наблюдаемый")
    _patch_model(monkeypatch)

    await _login(api_client, app_fixture, "developer", email_suffix="dev-owner")
    own = await _agent_run(api_client, agent_profile_id=profile_id)
    assert own.status_code == 200
    own_conversation = own.json()["conversation_id"]

    api_client.cookies.clear()
    await _login(api_client, app_fixture, "admin", email_suffix="admin-drilldown")
    resp = await api_client.get(f"{PROFILES_PATH}/{profile_id}/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "workspace"
    assert body["total"] == 1
    entry = body["conversations"][0]
    assert entry["id"] == own_conversation
    # Чужой диалог: заголовок (производное переписки) не отдаётся.
    assert entry["title"] is None
    # Расход — метаданные из существующего usage_event.conversation_id.
    assert entry["requests"] == 1
    assert entry["tokens_in"] == 3
    assert entry["tokens_out"] == 2
    # Состав полей фиксирован: текста сообщений в схеме нет вовсе.
    assert set(entry) == {
        "id",
        "user_id",
        "title",
        "archived",
        "created_at",
        "last_activity_at",
        "requests",
        "tokens_in",
        "tokens_out",
        "cost",
    }


@pytest.mark.asyncio
async def test_drilldown_unknown_profile_is_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login(api_client, app_fixture, "admin", email_suffix="admin-drill404")
    resp = await api_client.get(f"{PROFILES_PATH}/ghost/conversations")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# решение 7: остановка прогона
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_endpoint_sets_flag_and_writes_audit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-stop")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Останавливаемый")
    _patch_model(monkeypatch)

    first = await _agent_run(api_client, agent_profile_id=profile_id)
    conversation_id = first.json()["conversation_id"]

    resp = await api_client.post(f"/api/conversations/{conversation_id}/stop")
    assert resp.status_code == 200
    assert resp.json()["stop_requested"] is True
    assert resp.json()["agent_profile_id"] == profile_id

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.stop_requested is True
        audits = (await session.execute(select(AuditLog))).scalars().all()
    stop_audits = [a for a in audits if a.action == "agent.conversation.stop_requested"]
    assert len(stop_audits) == 1
    assert stop_audits[0].meta is not None
    assert stop_audits[0].meta["agent_profile_id"] == profile_id


@pytest.mark.asyncio
async def test_stop_is_noop_on_repeat_and_own_conversations_only(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повторное нажатие записи не плодит; чужой диалог — 404."""
    pytest.importorskip("langgraph")
    await _seed_models(app_fixture)
    _patch_model(monkeypatch)

    await _login(api_client, app_fixture, "developer", email_suffix="dev-stop-repeat")
    first = await _agent_run(api_client, model_alias=_MODEL_A)
    conversation_id = first.json()["conversation_id"]

    assert (await api_client.post(f"/api/conversations/{conversation_id}/stop")).status_code == 200
    assert (await api_client.post(f"/api/conversations/{conversation_id}/stop")).status_code == 200

    api_client.cookies.clear()
    await _login(api_client, app_fixture, "developer", email_suffix="dev-stop-other")
    assert (await api_client.post(f"/api/conversations/{conversation_id}/stop")).status_code == 404

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        audits = (await session.execute(select(AuditLog))).scalars().all()
    stop_audits = [a for a in audits if a.action == "agent.conversation.stop_requested"]
    assert len(stop_audits) == 1


@pytest.mark.asyncio
async def test_stop_rejects_chat_mode_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Диалогу обычного чата нечего останавливать — явный 400."""
    await _login(api_client, app_fixture, "developer", email_suffix="dev-stop-chat")
    created = await api_client.post("/api/conversations", json={"title": "Обычный чат"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    resp = await api_client.post(f"/api/conversations/{conversation_id}/stop")
    assert resp.status_code == 400
    assert resp.json()["constraint"]["mode"] == "chat"


@pytest.mark.asyncio
async def test_ad_hoc_conversation_has_no_profile(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 9: ad-hoc путь сохраняется и не привязывает профиль."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-adhoc")
    await _seed_models(app_fixture)
    _patch_model(monkeypatch)

    resp = await _agent_run(api_client, model_alias=_MODEL_A)
    assert resp.status_code == 200
    conversation_id = resp.json()["conversation_id"]

    details = await api_client.get(f"/api/conversations/{conversation_id}")
    assert details.status_code == 200
    assert details.json()["mode"] == "agent"
    assert details.json()["agent_profile_id"] is None
    assert details.json()["stop_requested"] is False


@pytest.mark.asyncio
async def test_profile_switch_does_not_move_existing_conversation(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 2: профиль диалога не подменяется профилем из запроса.

    Другой профиль в запросе — явный отказ (400), а не молчаливое
    продолжение со старым: клиент обязан узнать, что запрошенной
    конфигурации не будет. Тот же профиль — штатное продолжение.
    """
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-switch")
    ids = await _seed_models(app_fixture)
    first_profile = await _seed_profile(app_fixture, ids[_MODEL_A], name="Первый")
    second_profile = await _seed_profile(app_fixture, ids[_MODEL_B], name="Второй")
    calls = _patch_model(monkeypatch)

    started = await _agent_run(api_client, agent_profile_id=first_profile)
    conversation_id = started.json()["conversation_id"]

    switched = await _agent_run(
        api_client, conversation_id=conversation_id, agent_profile_id=second_profile
    )
    assert switched.status_code == 400
    assert switched.json()["error"] == "agent_profile_conflict"
    assert switched.json()["constraint"]["reason"] == "conversation_profile"
    assert switched.json()["constraint"]["agent_profile_id"] == first_profile
    assert switched.json()["constraint"]["requested_agent_profile_id"] == second_profile

    # Тот же профиль в запросе — не конфликт, а подтверждение выбора.
    same = await _agent_run(
        api_client, conversation_id=conversation_id, agent_profile_id=first_profile
    )
    assert same.status_code == 200
    assert same.json()["model"] == _MODEL_A
    # Один вызов провайдера: подмена профиля до него не дошла.
    assert calls["models"] == [_UPSTREAM_A, _UPSTREAM_A]

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.agent_profile_id == first_profile


@pytest.mark.asyncio
async def test_ad_hoc_conversation_cannot_be_attached_to_profile(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ad-hoc диалог не переводится на профиль: конфигурация фиксирована.

    Иначе профиль сменил бы модель существующего диалога и задним числом
    перенёс бы его историю в drill-down другого профиля.
    """
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "developer", email_suffix="dev-attach")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Прикрепляемый")
    calls = _patch_model(monkeypatch)

    ad_hoc = await _agent_run(api_client, model_alias=_MODEL_A)
    assert ad_hoc.status_code == 200
    conversation_id = ad_hoc.json()["conversation_id"]

    resp = await _agent_run(
        api_client, conversation_id=conversation_id, agent_profile_id=profile_id
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "agent_profile_conflict"
    assert resp.json()["constraint"]["agent_profile_id"] is None

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.agent_profile_id is None
    assert calls["models"] == [_UPSTREAM_A]


# ---------------------------------------------------------------------------
# решение 8: удаление профиля
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_profile_without_conversations_succeeds(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _login(api_client, app_fixture, "admin", email_suffix="admin-delete-ok")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Удаляемый")

    resp = await api_client.delete(f"{PROFILES_PATH}/{profile_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        assert await session.get(AgentProfile, profile_id) is None
        audits = (await session.execute(select(AuditLog))).scalars().all()
    profile_audits = [a for a in audits if a.action == "agent_profile.changed"]
    assert len(profile_audits) == 1
    assert profile_audits[0].meta is not None
    assert profile_audits[0].meta["new"] is None


@pytest.mark.asyncio
async def test_delete_profile_with_conversations_is_409(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Решение 8: 409, каскада нет; основной путь — ``enabled=false``."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "admin", email_suffix="admin-delete-409")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="С диалогами")
    calls = _patch_model(monkeypatch)

    run = await _agent_run(api_client, agent_profile_id=profile_id)
    assert run.status_code == 200
    conversation_id = run.json()["conversation_id"]
    assert calls["models"] == [_UPSTREAM_A]

    resp = await api_client.delete(f"{PROFILES_PATH}/{profile_id}")
    assert resp.status_code == 409
    assert resp.json()["constraint"]["conversations_count"] == 1

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        assert await session.get(AgentProfile, profile_id) is not None
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        assert conversation.agent_profile_id == profile_id

    # Отключение работает и не трогает диалоги.
    disabled = await api_client.put(
        f"{PROFILES_PATH}/{profile_id}",
        json={"name": "С диалогами", "model_id": ids[_MODEL_A], "enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    available = await api_client.get(AVAILABLE_PATH)
    assert [entry["id"] for entry in available.json()["profiles"]] == []


@pytest.mark.asyncio
async def test_update_profile_revalidates_model_and_skill(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Правка профиля проходит те же проверки, что создание."""
    await _login(api_client, app_fixture, "admin", email_suffix="admin-update")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Правимый")

    resp = await api_client.put(
        f"{PROFILES_PATH}/{profile_id}",
        json={"name": "Правимый", "model_id": ids[_MODEL_B], "enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json()["model_id"] == ids[_MODEL_B]
    assert resp.json()["model_alias"] == _MODEL_B

    ghost_skill = await api_client.put(
        f"{PROFILES_PATH}/{profile_id}",
        json={"name": "Правимый", "model_id": ids[_MODEL_B], "skill_id": "ghost"},
    )
    assert ghost_skill.status_code == 404

    no_tools = await _seed_models(app_fixture, aliases=("local/no-tools-2",), supports_tools=False)
    bad_model = await api_client.put(
        f"{PROFILES_PATH}/{profile_id}",
        json={"name": "Правимый", "model_id": no_tools["local/no-tools-2"]},
    )
    assert bad_model.status_code == 400


@pytest.mark.asyncio
async def test_profile_limit_per_workspace(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORQION_AGENT_PROFILES_MAX_PER_WORKSPACE", "2")
    await _login(api_client, app_fixture, "admin", email_suffix="admin-limit")
    ids = await _seed_models(app_fixture)

    for index in range(2):
        resp = await api_client.post(
            PROFILES_PATH, json={"name": f"Профиль {index}", "model_id": ids[_MODEL_A]}
        )
        assert resp.status_code == 201
    over = await api_client.post(
        PROFILES_PATH, json={"name": "Профиль 2", "model_id": ids[_MODEL_A]}
    )
    assert over.status_code == 422
    assert "профилей" in over.text


@pytest.mark.asyncio
async def test_disabled_profile_blocks_its_conversations(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отключение профиля отключает и его диалоги: это смысл выключения."""
    pytest.importorskip("langgraph")
    await _login(api_client, app_fixture, "admin", email_suffix="admin-disable")
    ids = await _seed_models(app_fixture)
    profile_id = await _seed_profile(app_fixture, ids[_MODEL_A], name="Выключаемый")
    calls = _patch_model(monkeypatch)

    first = await _agent_run(api_client, agent_profile_id=profile_id)
    conversation_id = first.json()["conversation_id"]

    disabled = await api_client.put(
        f"{PROFILES_PATH}/{profile_id}",
        json={"name": "Выключаемый", "model_id": ids[_MODEL_A], "enabled": False},
    )
    assert disabled.status_code == 200

    resp = await _agent_run(api_client, conversation_id=conversation_id)
    assert resp.status_code == 400
    assert resp.json()["error"] == "agent_profile_not_available"
    assert calls["models"] == [_UPSTREAM_A]
