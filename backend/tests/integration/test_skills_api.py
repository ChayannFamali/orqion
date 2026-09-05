"""Тесты API скиллов — пакетов конфигурации агентного прогона (Т-508).

Пункт 1 приёмки и решения 3, 4, 6 дизайн-ревью:

- админский каталог и записи — только со способностью ``manage_skills``
  (без права 404, паттерн ``manage_mcp_servers``);
- список для выбора в диалоге — всем аутентифицированным, только
  включённые и только поля, нужные для выбора;
- лимиты (число скиллов, длина текста) — 422;
- схема отвергает неизвестные поля (``extra="forbid"``): признака
  «доверенности» инструмента в скилле быть не может;
- аудит ``agent_skill.changed`` — со старым и новым значением полей, но
  БЕЗ текста промпта (только его длина): ADR-21 п. 2 запрещает писать
  содержимое в журнал, а журнал бессрочный и append-only.
"""

from __future__ import annotations

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.db.models import AgentSkill, AuditLog, Role, User
from fastapi import FastAPI
from sqlalchemy import select


async def _login_as_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> str:
    """Создаёт admin-пользователя и логинится через cookie."""
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        ws_id = app_fixture.state.workspace_id
        role = Role(
            workspace_id=ws_id,
            name="admin",
            is_builtin=True,
            policy=BUILTIN_ROLES["admin"].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=ws_id,
            email="skills-admin@orqion.local",
            password_hash=hash_password("admin-password-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _login_as_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str,
) -> str:
    """Логинит пользователя с посевной ролью (без manage_skills)."""
    from app.policy.presets import BUILTIN_ROLES

    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(
            workspace_id=ws_id,
            name=role_name,
            is_builtin=True,
            policy=BUILTIN_ROLES[role_name].model_dump(),
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=ws_id,
            email=f"skills-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, ws_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _create_skill(
    api_client: httpx.AsyncClient,
    name: str = "Разбор документов",
    **extra: object,
) -> httpx.Response:
    payload: dict[str, object] = {"name": name}
    payload.update(extra)
    return await api_client.post("/api/skills", json=payload)


async def _audit_rows(app_fixture: FastAPI) -> list[AuditLog]:
    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        return list((await session.execute(select(AuditLog))).scalars().all())


@pytest.mark.asyncio
async def test_create_skill(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Создание: все поля сохраняются, пустой список инструментов допустим."""
    await _login_as_admin(api_client, app_fixture)

    response = await _create_skill(
        api_client,
        description="Для типового сценария",
        prompt_text="Отвечай строго по найденным документам.",
        tools=["search_corpus", "demo-build.get_build_status"],
        default_max_tokens=512,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Разбор документов"
    assert body["description"] == "Для типового сценария"
    assert body["prompt_text"] == "Отвечай строго по найденным документам."
    assert body["tools"] == ["search_corpus", "demo-build.get_build_status"]
    assert body["default_max_tokens"] == 512
    assert body["enabled"] is True

    factory = app_fixture.state.db_session_factory
    async with factory() as session:
        row = (await session.execute(select(AgentSkill))).scalar_one()
        assert row.tools == ["search_corpus", "demo-build.get_build_status"]


@pytest.mark.asyncio
async def test_create_skill_minimal_defaults_to_zero_tools(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Скилл без инструментов создаётся с пустым списком = ноль инструментов."""
    await _login_as_admin(api_client, app_fixture)

    response = await _create_skill(api_client, prompt_text="Только инструкции")
    assert response.status_code == 201, response.text
    assert response.json()["tools"] == []
    assert response.json()["default_max_tokens"] is None


@pytest.mark.asyncio
async def test_create_skill_deduplicates_tools(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Дубликаты имён в списке схлопываются с сохранением порядка."""
    await _login_as_admin(api_client, app_fixture)

    response = await _create_skill(
        api_client,
        tools=["search_corpus", "demo.echo", "search_corpus"],
    )
    assert response.status_code == 201, response.text
    assert response.json()["tools"] == ["search_corpus", "demo.echo"]


@pytest.mark.asyncio
async def test_create_skill_duplicate_name_conflict(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Имя уникально в рабочей области — второй скилл с тем же именем отклонён."""
    await _login_as_admin(api_client, app_fixture)

    first = await _create_skill(api_client, name="Один")
    assert first.status_code == 201

    second = await _create_skill(api_client, name="Один")
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_create_skill_rejects_unknown_fields(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Решение 3: признака «доверенности» в схеме нет — лишнее поле даёт 422."""
    await _login_as_admin(api_client, app_fixture)

    for bad_field in ("auto_approve", "trusted_tools", "allow_destructive", "execute"):
        response = await _create_skill(api_client, name=f"Скилл-{bad_field}", **{bad_field: True})
        assert response.status_code == 422, (bad_field, response.text)


@pytest.mark.parametrize(
    "bad_tools",
    [
        [""],
        ["  "],
        ["search corpus"],
        ["demo.echo\nsearch_corpus"],
    ],
)
@pytest.mark.asyncio
async def test_create_skill_rejects_malformed_tool_names(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    bad_tools: list[str],
) -> None:
    """Формат имён проверяется, состав реестра — нет (см. решение 6)."""
    await _login_as_admin(api_client, app_fixture)

    response = await _create_skill(api_client, tools=bad_tools)
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_create_skill_accepts_unknown_tool_name(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Имя инструмента, которого нет в реестре, сохраняется — расходование в прогоне."""
    await _login_as_admin(api_client, app_fixture)

    response = await _create_skill(api_client, tools=["not-registered-yet.tool"])
    assert response.status_code == 201, response.text
    assert response.json()["tools"] == ["not-registered-yet.tool"]


@pytest.mark.asyncio
async def test_create_skill_prompt_too_long(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Лимит длины текста — настройка приложения; превышение — 422."""
    await _login_as_admin(api_client, app_fixture)

    monkeypatch.setenv("ORQION_SKILL_PROMPT_MAX_CHARS", "100")

    ok = await _create_skill(api_client, name="В пределе", prompt_text="а" * 100)
    assert ok.status_code == 201, ok.text

    too_long = await _create_skill(api_client, name="Сверх", prompt_text="а" * 101)
    assert too_long.status_code == 422, too_long.text


@pytest.mark.asyncio
async def test_create_skill_count_limit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Лимит числа скиллов на рабочую область — настройка; превышение — 422."""
    await _login_as_admin(api_client, app_fixture)

    monkeypatch.setenv("ORQION_AGENT_SKILLS_MAX_PER_WORKSPACE", "2")

    assert (await _create_skill(api_client, name="Первый")).status_code == 201
    assert (await _create_skill(api_client, name="Второй")).status_code == 201

    third = await _create_skill(api_client, name="Третий")
    assert third.status_code == 422, third.text


@pytest.mark.asyncio
async def test_list_skills_admin_catalog_includes_disabled(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Админский каталог: все скиллы рабочей области, включая выключенные."""
    await _login_as_admin(api_client, app_fixture)

    assert (await _create_skill(api_client, name="zeta")).status_code == 201
    disabled = await _create_skill(api_client, name="alpha", enabled=False)
    assert disabled.status_code == 201

    response = await api_client.get("/api/skills")
    assert response.status_code == 200
    skills = response.json()["skills"]
    assert [s["name"] for s in skills] == ["alpha", "zeta"]
    assert skills[0]["enabled"] is False
    # Полные поля — иначе выключенный скилл нельзя включить обратно.
    assert "prompt_text" in skills[0]
    assert "tools" in skills[0]


@pytest.mark.asyncio
async def test_available_skills_only_enabled_and_minimal_fields(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Список для выбора: только включённые, только поля выбора, без инструкций."""
    await _login_as_admin(api_client, app_fixture)
    assert (
        await _create_skill(api_client, name="Видимый", prompt_text="секретный текст")
    ).status_code == 201
    assert (await _create_skill(api_client, name="Скрытый", enabled=False)).status_code == 201

    response = await api_client.get("/api/skills/available")
    assert response.status_code == 200
    skills = response.json()["skills"]
    assert [s["name"] for s in skills] == ["Видимый"]
    assert set(skills[0]) == {"id", "name", "description"}
    assert "prompt_text" not in skills[0]
    assert "tools" not in skills[0]


@pytest.mark.asyncio
async def test_update_skill_fields(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Правка заменяет поля целиком (паттерн Т-507), имя можно менять."""
    await _login_as_admin(api_client, app_fixture)
    created = await _create_skill(api_client, name="Старое", tools=["search_corpus"])
    skill_id = created.json()["id"]

    response = await api_client.put(
        f"/api/skills/{skill_id}",
        json={
            "name": "Новое",
            "description": "обновлено",
            "prompt_text": "новый текст",
            "tools": [],
            "default_max_tokens": 256,
            "enabled": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Новое"
    assert body["tools"] == []
    assert body["default_max_tokens"] == 256
    assert body["enabled"] is False


@pytest.mark.asyncio
async def test_update_skill_rejects_unknown_fields(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Правка тоже отвергает неподписанные поля — обход подтверждения невозможен."""
    await _login_as_admin(api_client, app_fixture)
    created = await _create_skill(api_client)
    skill_id = created.json()["id"]

    response = await api_client.put(
        f"/api/skills/{skill_id}",
        json={
            "name": "Имя",
            "description": "",
            "prompt_text": "",
            "tools": [],
            "default_max_tokens": None,
            "enabled": True,
            "auto_approve": ["demo.drop_cache"],
        },
    )
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_update_missing_skill_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    await _login_as_admin(api_client, app_fixture)

    response = await api_client.put(
        "/api/skills/no-such-id",
        json={
            "name": "Имя",
            "description": "",
            "prompt_text": "",
            "tools": [],
            "default_max_tokens": None,
            "enabled": True,
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_skill(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Удаление убирает скилл из каталога; повторное удаление — 404."""
    await _login_as_admin(api_client, app_fixture)
    created = await _create_skill(api_client)
    skill_id = created.json()["id"]

    deleted = await api_client.delete(f"/api/skills/{skill_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    listing = await api_client.get("/api/skills")
    assert listing.json()["skills"] == []

    again = await api_client.delete(f"/api/skills/{skill_id}")
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_audit_written_without_prompt_text(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Аудит создания: состав полей есть, текста промпта нет — только длина."""
    await _login_as_admin(api_client, app_fixture)

    secret_text = "СЕКРЕТНАЯ-СТРОКА-ИНСТРУКЦИИ"
    created = await _create_skill(
        api_client,
        name="С аудитом",
        prompt_text=secret_text,
        tools=["search_corpus"],
    )
    skill_id = created.json()["id"]

    rows = await _audit_rows(app_fixture)
    changes = [r for r in rows if r.action == "agent_skill.changed"]
    assert len(changes) == 1
    record = changes[0]
    assert record.object_type == "agent_skill"
    assert record.object_id == skill_id
    assert record.meta is not None
    assert record.meta["old"] is None
    new = record.meta["new"]
    assert isinstance(new, dict)
    assert new["name"] == "С аудитом"
    assert new["tools"] == ["search_corpus"]
    assert new["prompt_chars"] == len(secret_text)
    assert secret_text not in str(record.meta)


@pytest.mark.asyncio
async def test_audit_update_has_old_and_new(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Аудит правки: старое и новое значение полей (паттерн Т-506)."""
    await _login_as_admin(api_client, app_fixture)
    created = await _create_skill(api_client, name="До", tools=["search_corpus"])
    skill_id = created.json()["id"]

    await api_client.put(
        f"/api/skills/{skill_id}",
        json={
            "name": "После",
            "description": "",
            "prompt_text": "",
            "tools": [],
            "default_max_tokens": None,
            "enabled": False,
        },
    )

    rows = await _audit_rows(app_fixture)
    changes = [r for r in rows if r.action == "agent_skill.changed"]
    assert len(changes) == 2
    update = changes[1]
    assert update.meta is not None
    old = update.meta["old"]
    new = update.meta["new"]
    assert isinstance(old, dict)
    assert isinstance(new, dict)
    assert old["name"] == "До"
    assert new["name"] == "После"
    assert old["enabled"] is True
    assert new["enabled"] is False


@pytest.mark.asyncio
async def test_audit_noop_update_not_written(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Сохранение без изменений запись аудита не создаёт (паттерн Т-506)."""
    await _login_as_admin(api_client, app_fixture)
    created = await _create_skill(api_client, name="Без изменений")
    skill_id = created.json()["id"]

    await api_client.put(
        f"/api/skills/{skill_id}",
        json={
            "name": "Без изменений",
            "description": "",
            "prompt_text": "",
            "tools": [],
            "default_max_tokens": None,
            "enabled": True,
        },
    )

    rows = await _audit_rows(app_fixture)
    changes = [r for r in rows if r.action == "agent_skill.changed"]
    assert len(changes) == 1  # только создание


@pytest.mark.asyncio
async def test_audit_delete_records_old_state(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Аудит удаления: старое состояние сохранено, нового нет."""
    await _login_as_admin(api_client, app_fixture)
    created = await _create_skill(api_client, name="На удаление")
    skill_id = created.json()["id"]

    await api_client.delete(f"/api/skills/{skill_id}")

    rows = await _audit_rows(app_fixture)
    changes = [r for r in rows if r.action == "agent_skill.changed"]
    assert len(changes) == 2
    deletion = changes[1]
    assert deletion.meta is not None
    assert deletion.meta["new"] is None
    old = deletion.meta["old"]
    assert isinstance(old, dict)
    assert old["name"] == "На удаление"


@pytest.mark.asyncio
async def test_skills_catalog_requires_capability(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Без manage_skills каталог и записи — 404 (существование не раскрывается)."""
    await _login_as_admin(api_client, app_fixture)
    created = await _create_skill(api_client)
    skill_id = created.json()["id"]

    await _login_as_role(api_client, app_fixture, "developer")

    assert (await api_client.get("/api/skills")).status_code == 404
    assert (await _create_skill(api_client, name="Чужой")).status_code == 404
    assert (
        await api_client.put(
            f"/api/skills/{skill_id}",
            json={
                "name": "Перехват",
                "description": "",
                "prompt_text": "",
                "tools": [],
                "default_max_tokens": None,
                "enabled": True,
            },
        )
    ).status_code == 404
    assert (await api_client.delete(f"/api/skills/{skill_id}")).status_code == 404


@pytest.mark.asyncio
async def test_available_skills_visible_to_non_admin(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Решение 4: список для выбора доступен без manage_skills."""
    await _login_as_admin(api_client, app_fixture)
    assert (await _create_skill(api_client, name="Общий")).status_code == 201

    await _login_as_role(api_client, app_fixture, "developer")

    response = await api_client.get("/api/skills/available")
    assert response.status_code == 200
    assert [s["name"] for s in response.json()["skills"]] == ["Общий"]


@pytest.mark.asyncio
async def test_skills_require_auth(
    api_client: httpx.AsyncClient,
) -> None:
    """Без сессии все эндпоинты скиллов недоступны."""
    assert (await api_client.get("/api/skills")).status_code == 401
    assert (await api_client.get("/api/skills/available")).status_code == 401
    assert (await api_client.post("/api/skills", json={"name": "x"})).status_code == 401
