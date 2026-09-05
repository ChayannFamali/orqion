"""Т-508: сужение реестра инструментов скиллом и отказ на недоступный скилл.

Проверяются решения 2, 5 и 6 дизайн-ревью:

- скилл — чистый фильтр пересечения: не добавляет инструмент вне реестра,
  не меняет транспортные данные и признак блока К2/К3;
- пустой список инструментов скилла даёт НОЛЬ инструментов (правка
  пользователя), а не «не сужать»;
- несуществующий или отключённый ``skill_id`` — явный отказ, не тихий
  откат к прогону без скилла (правка пользователя);
- причина отброшенного инструмента различает блок по классу данных и
  отсутствие в реестре.
"""

from __future__ import annotations

import pytest
from app.agent.skills import apply_skill, resolve_skill_for_run, skill_drop_reason
from app.agent.tools import ResolvedTools, ServerEndpoint, ToolSpec
from app.auth.passwords import hash_password
from app.db.models import AgentSkill, Role, User, Workspace
from app.errors import SkillNotAvailable
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _spec(name: str, *, server_name: str | None = None, destructive: bool = False) -> ToolSpec:
    if server_name is None:
        return ToolSpec(
            name=name,
            description=f"встроенный {name}",
            parameters={"type": "object", "properties": {}},
            destructive=destructive,
        )
    return ToolSpec(
        name=name,
        description=f"внешний {name}",
        parameters={"type": "object", "properties": {}},
        destructive=destructive,
        source=f"mcp:{server_name}",
        server_name=server_name,
        mcp_tool_name=name.split(".", 1)[1],
    )


def _skill(**kwargs: object) -> AgentSkill:
    """Экземпляр скилла без записи в БД — ``apply_skill`` чистая функция."""
    base: dict[str, object] = {
        "id": "skill-1",
        "workspace_id": "ws-1",
        "name": "Разбор",
        "description": "",
        "prompt_text": "",
        "tools": [],
        "default_max_tokens": None,
        "enabled": True,
    }
    base.update(kwargs)
    return AgentSkill(**base)


def _registry(
    specs: list[ToolSpec],
    *,
    servers: dict[str, ServerEndpoint] | None = None,
    blocked_external: str | None = None,
) -> ResolvedTools:
    return ResolvedTools(
        specs=specs,
        servers=servers if servers is not None else {},
        blocked_external=blocked_external,
    )


def test_apply_skill_intersects_with_registry() -> None:
    """Сужение оставляет только инструменты, которые есть и в реестре, и в скилле."""
    registry = _registry(
        [
            _spec("search_corpus"),
            _spec("demo.echo", server_name="demo"),
            _spec("demo.drop_cache", server_name="demo", destructive=True),
        ],
        servers={"demo": ServerEndpoint(url="http://x/mcp", api_key_enc=None)},
    )
    skill = _skill(tools=["search_corpus", "demo.echo"])

    narrowed, unavailable = apply_skill(registry, skill)

    assert [s.name for s in narrowed.specs] == ["search_corpus", "demo.echo"]
    assert unavailable == []
    # Признак деструктивности берётся из реестра, скилл его не меняет.
    echo = narrowed.spec_by_name("demo.echo")
    assert echo is not None
    assert echo.source == "mcp:demo"


def test_apply_skill_never_adds_tool_outside_registry() -> None:
    """Инструмент, которого нет в реестре, в прогон не попадает (решение 2)."""
    registry = _registry([_spec("search_corpus")])
    skill = _skill(tools=["search_corpus", "ghost.tool", "demo.echo"])

    narrowed, unavailable = apply_skill(registry, skill)

    assert [s.name for s in narrowed.specs] == ["search_corpus"]
    assert unavailable == ["ghost.tool", "demo.echo"]


def test_apply_skill_empty_tools_gives_zero_tools() -> None:
    """Пустой список = НОЛЬ инструментов, включая встроенный поиск (решение 6)."""
    registry = _registry(
        [
            _spec("search_corpus"),
            _spec("demo.echo", server_name="demo"),
        ],
        servers={"demo": ServerEndpoint(url="http://x/mcp", api_key_enc=None)},
    )
    skill = _skill(tools=[])

    narrowed, unavailable = apply_skill(registry, skill)

    assert narrowed.specs == []
    assert narrowed.schemas() == []
    assert unavailable == []
    # Недостижимый эндпоинт не сохраняется.
    assert narrowed.servers == {}


def test_apply_skill_narrows_servers_to_kept_tools() -> None:
    """Транспортные данные сужаются до серверов оставшихся инструментов."""
    registry = _registry(
        [
            _spec("search_corpus"),
            _spec("demo.echo", server_name="demo"),
            _spec("wiki.lookup", server_name="wiki"),
        ],
        servers={
            "demo": ServerEndpoint(url="http://demo/mcp", api_key_enc=None),
            "wiki": ServerEndpoint(url="http://wiki/mcp", api_key_enc="enc"),
        },
    )
    skill = _skill(tools=["demo.echo"])

    narrowed, _ = apply_skill(registry, skill)

    assert list(narrowed.servers) == ["demo"]
    assert narrowed.servers["demo"].url == "http://demo/mcp"


def test_apply_skill_preserves_blocked_external_flag() -> None:
    """Признак блока К2/К3 переносится: сужение не снимает гарантию ADR-21."""
    registry = _registry([_spec("search_corpus")], blocked_external="К2")
    skill = _skill(tools=["search_corpus"])

    narrowed, _ = apply_skill(registry, skill)

    assert narrowed.blocked_external == "К2"


def test_apply_skill_does_not_mutate_input_registry() -> None:
    """Чистая функция: входной реестр остаётся неизменным."""
    specs = [_spec("search_corpus"), _spec("demo.echo", server_name="demo")]
    registry = _registry(
        specs,
        servers={"demo": ServerEndpoint(url="http://x/mcp", api_key_enc=None)},
    )
    skill = _skill(tools=["demo.echo"])

    narrowed, _ = apply_skill(registry, skill)

    assert narrowed is not registry
    assert [s.name for s in registry.specs] == ["search_corpus", "demo.echo"]
    assert list(registry.servers) == ["demo"]


def test_apply_skill_deduplicates_tools_preserving_order() -> None:
    """Дубли в списке скилла не ломают порядок реестра и не дают повторов."""
    registry = _registry([_spec("search_corpus"), _spec("demo.echo", server_name="demo")])
    skill = _skill(tools=["demo.echo", "search_corpus", "demo.echo"])

    narrowed, unavailable = apply_skill(registry, skill)

    # Порядок канонический — как в реестре, а не как в списке скилла.
    assert [s.name for s in narrowed.specs] == ["search_corpus", "demo.echo"]
    assert unavailable == []


def test_skill_drop_reason_distinguishes_k2_block() -> None:
    """Внешний инструмент при К2/К3 отброшен по классу данных, не «не найден»."""
    registry = _registry([_spec("search_corpus")], blocked_external="К2")

    assert skill_drop_reason("demo.echo", registry) == "blocked_external"
    # Встроенный инструмент без неймспейса блоком класса данных не отброшен.
    assert skill_drop_reason("search_corpus", registry) == "not_in_registry"


def test_skill_drop_reason_without_block() -> None:
    """Без блока К2/К3 причина укрупнённая — инструмента нет в реестре."""
    registry = _registry([_spec("search_corpus")])

    assert skill_drop_reason("demo.echo", registry) == "not_in_registry"
    assert skill_drop_reason("ghost.tool", registry) == "not_in_registry"


async def _seed_workspace(db_session: AsyncSession) -> str:
    ws = Workspace(name="skills-test")
    db_session.add(ws)
    await db_session.flush()
    role = Role(
        workspace_id=ws.id,
        name="skills-role",
        is_builtin=False,
        policy={"models": ["*"], "corpora": ["*"]},
    )
    db_session.add(role)
    await db_session.flush()
    user = User(
        workspace_id=ws.id,
        email="skills@orqion.local",
        password_hash=hash_password("pass-123"),
        role_id=role.id,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    return ws.id


@pytest.mark.asyncio
async def test_resolve_skill_returns_enabled_skill(db_session: AsyncSession) -> None:
    workspace_id = await _seed_workspace(db_session)
    skill = AgentSkill(
        workspace_id=workspace_id, name="Разбор", prompt_text="Будь точен", tools=["search_corpus"]
    )
    db_session.add(skill)
    await db_session.commit()

    resolved = await resolve_skill_for_run(db_session, workspace_id, skill.id)

    assert resolved.id == skill.id
    assert resolved.prompt_text == "Будь точен"


@pytest.mark.asyncio
async def test_resolve_skill_missing_raises_400(db_session: AsyncSession) -> None:
    """Несуществующий skill_id — явный отказ 400, не тихий прогон без скилла."""
    workspace_id = await _seed_workspace(db_session)

    with pytest.raises(SkillNotAvailable) as exc_info:
        await resolve_skill_for_run(db_session, workspace_id, "no-such-skill")

    assert exc_info.value.status_code == 400
    assert exc_info.value.error_code == "skill_not_available"
    assert exc_info.value.reason == "Скилл не найден или отключён"
    assert exc_info.value.constraint == {"skill_id": "no-such-skill"}


@pytest.mark.asyncio
async def test_resolve_skill_disabled_raises_400(db_session: AsyncSession) -> None:
    """Отключённый скилл недоступен для прогона — тот же явный отказ."""
    workspace_id = await _seed_workspace(db_session)
    skill = AgentSkill(workspace_id=workspace_id, name="Выключен", enabled=False)
    db_session.add(skill)
    await db_session.commit()

    with pytest.raises(SkillNotAvailable):
        await resolve_skill_for_run(db_session, workspace_id, skill.id)


@pytest.mark.asyncio
async def test_resolve_skill_isolated_by_workspace(db_session: AsyncSession) -> None:
    """Скилл чужой рабочей области не находится (workspace_id, ADR-3)."""
    workspace_id = await _seed_workspace(db_session)
    other = Workspace(name="other")
    db_session.add(other)
    await db_session.flush()
    skill = AgentSkill(workspace_id=other.id, name="Чужой")
    db_session.add(skill)
    await db_session.commit()

    with pytest.raises(SkillNotAvailable):
        await resolve_skill_for_run(db_session, workspace_id, skill.id)

    # В своей области скилл существует — значит отказ именно по границе.
    found = (await db_session.execute(select(AgentSkill).where(AgentSkill.id == skill.id))).scalar()
    assert found is not None
