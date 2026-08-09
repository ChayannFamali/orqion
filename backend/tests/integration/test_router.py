"""Интеграционные тесты маршрутизации: load_rules, ensure_default, data_class + БД."""

from __future__ import annotations

import pytest
from app.db.models import Model, Provider, RoutingRule
from app.db.workspace import ensure_default_workspace
from app.errors import NoRouteAvailable
from app.router.bootstrap import ensure_default_routing_rules
from app.router.models import RouteContext
from app.router.service import load_candidate_models, load_rules, select_model
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_provider_and_models(
    session: AsyncSession,
    workspace_id: str,
) -> tuple[Provider, list[Model]]:
    provider = Provider(
        workspace_id=workspace_id,
        kind="ollama",
        base_url="http://localhost:11434",
        enabled=True,
        capabilities={},
    )
    session.add(provider)
    await session.flush()

    models = []
    for alias, locality, enabled in [
        ("local/qwen3-8b", "local", True),
        ("local/qwen3-14b", "local", True),
        ("external/gpt-4", "external", True),
        ("local/disabled", "local", False),
    ]:
        m = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias=alias,
            upstream_name=alias,
            locality=locality,
            enabled=enabled,
        )
        session.add(m)
        models.append(m)
    await session.flush()
    return provider, models


@pytest.mark.asyncio
async def test_ensure_default_routing_rules_creates_rules(
    db_session: AsyncSession,
) -> None:
    """Seed создаёт правила, повторный вызов не дублирует."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    await ensure_default_routing_rules(db_session, ws_id)
    await db_session.flush()

    rules = await load_rules(db_session, ws_id)
    assert len(rules) == 4
    assert rules[0].is_default is False
    assert rules[0].is_terminal is True
    assert rules[0].when_corpus_class == ["К2", "К3"]
    assert rules[0].allow_locality == ["local"]
    assert rules[-1].is_default is True

    # Повторный вызов не дублирует
    await ensure_default_routing_rules(db_session, ws_id)
    await db_session.flush()
    rules2 = await load_rules(db_session, ws_id)
    assert len(rules2) == 4


@pytest.mark.asyncio
async def test_load_candidate_models_excludes_disabled(
    db_session: AsyncSession,
) -> None:
    """Включённые модели с включёнными провайдерами — остальные отбрасываются."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()
    await _seed_provider_and_models(db_session, ws_id)

    candidates = await load_candidate_models(db_session, ws_id)
    aliases = {m.alias for m in candidates}
    assert "local/qwen3-8b" in aliases
    assert "local/qwen3-14b" in aliases
    assert "external/gpt-4" in aliases
    assert "local/disabled" not in aliases


@pytest.mark.asyncio
async def test_load_candidate_models_excludes_disabled_provider(
    db_session: AsyncSession,
) -> None:
    """Модель включена, провайдер выключен → не попадает в кандидаты."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    provider = Provider(
        workspace_id=ws_id,
        kind="ollama",
        base_url="http://localhost:11434",
        enabled=False,
        capabilities={},
    )
    session = db_session
    session.add(provider)
    await session.flush()

    model = Model(
        workspace_id=ws_id,
        provider_id=provider.id,
        alias="local/gemma",
        upstream_name="gemma",
        locality="local",
        enabled=True,
    )
    session.add(model)
    await session.flush()

    candidates = await load_candidate_models(session, ws_id)
    aliases = {m.alias for m in candidates}
    assert "local/gemma" not in aliases


@pytest.mark.asyncio
async def test_default_rules_k3_routes_to_local(db_session: AsyncSession) -> None:
    """К3 + default rules → внешние модели исключены, выбирается локальная."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()
    await _seed_provider_and_models(db_session, ws_id)

    await ensure_default_routing_rules(db_session, ws_id)
    await db_session.flush()

    rules = await load_rules(db_session, ws_id)
    candidates = await load_candidate_models(db_session, ws_id)

    ctx = RouteContext(
        candidate_models=candidates,
        user_role_name="developer",
        corpus_data_class="К3",
    )
    decision = select_model(rules, ctx)
    assert decision.model.locality == "local"
    assert decision.model.alias == "local/qwen3-8b"


@pytest.mark.asyncio
async def test_default_rules_no_route_when_only_external_k3(
    db_session: AsyncSession,
) -> None:
    """К3 + только внешние модели → NoRouteAvailable."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    provider = Provider(
        workspace_id=ws_id,
        kind="openai",
        base_url="http://api.openai.com/v1",
        enabled=True,
        capabilities={},
    )
    db_session.add(provider)
    await db_session.flush()

    model = Model(
        workspace_id=ws_id,
        provider_id=provider.id,
        alias="external/gpt-4",
        upstream_name="gpt-4",
        locality="external",
        enabled=True,
    )
    db_session.add(model)
    await db_session.flush()

    await ensure_default_routing_rules(db_session, ws_id)
    await db_session.flush()

    rules = await load_rules(db_session, ws_id)
    candidates = await load_candidate_models(db_session, ws_id)

    ctx = RouteContext(
        candidate_models=candidates,
        user_role_name="developer",
        corpus_data_class="К3",
    )
    with pytest.raises(NoRouteAvailable):
        select_model(rules, ctx)


@pytest.mark.asyncio
async def test_custom_rule_narrows_by_role(db_session: AsyncSession) -> None:
    """Пользовательское правило для role=support сужает до одной модели."""
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()
    await _seed_provider_and_models(db_session, ws_id)

    # Стираем default и вставляем своё правило
    from sqlalchemy import delete

    await db_session.execute(delete(RoutingRule).where(RoutingRule.workspace_id == ws_id))
    await db_session.flush()

    custom = RoutingRule(
        workspace_id=ws_id,
        order=0,
        is_default=False,
        is_terminal=True,
        when_role="support",
        to_models=["local/qwen3-8b"],
        reason="support-only-8b",
    )
    db_session.add(custom)
    await db_session.flush()

    rules = await load_rules(db_session, ws_id)
    candidates = await load_candidate_models(db_session, ws_id)

    ctx = RouteContext(
        candidate_models=candidates,
        user_role_name="support",
    )
    decision = select_model(rules, ctx)
    assert decision.model.alias == "local/qwen3-8b"
    assert decision.reason == "support-only-8b"


@pytest.mark.asyncio
async def test_adr12_invariant_k3_blocks_external_without_rules(
    db_session: AsyncSession,
) -> None:
    """ADR-12: К3+внешние модели → отказ, даже если ВСЕ правила удалены из таблицы.

    Фильтр К2/К3→local — код в _filter_data_class, не строка в routing_rule.
    Удаление seed-правила К2/К3 из таблицы не должно менять поведение:
    внешние модели всё равно исключаются до прогона правил.
    """
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()

    provider = Provider(
        workspace_id=ws_id,
        kind="openai",
        base_url="http://api.openai.com/v1",
        enabled=True,
        capabilities={},
    )
    db_session.add(provider)
    await db_session.flush()

    for alias in ["external/gpt-4", "external/claude-sonnet"]:
        db_session.add(
            Model(
                workspace_id=ws_id,
                provider_id=provider.id,
                alias=alias,
                upstream_name=alias,
                locality="external",
                enabled=True,
            )
        )
    await db_session.flush()

    # Удаляем ВСЕ правила маршрутизации — включая seed К2/К3
    from sqlalchemy import delete

    await db_session.execute(delete(RoutingRule).where(RoutingRule.workspace_id == ws_id))
    await db_session.flush()

    # Подтверждаем: таблица пуста
    rules = await load_rules(db_session, ws_id)
    assert rules == []

    candidates = await load_candidate_models(db_session, ws_id)
    assert len(candidates) == 2  # обе внешние

    ctx = RouteContext(
        candidate_models=candidates,
        user_role_name="admin",
        corpus_data_class="К3",
    )
    with pytest.raises(NoRouteAvailable) as exc_info:
        select_model(rules, ctx)
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_adr12_invariant_k3_selects_local_even_with_external_allowing_rule(
    db_session: AsyncSession,
) -> None:
    """ADR-12: К3 выбирает локальную модель, даже если правило явно разрешает внешние.

    Правило to=["*"] (всё разрешено) не отменяет _filter_data_class:
    внешние модели исключены до прогона правил, правило не может их вернуть.
    """
    ws_id = await ensure_default_workspace(db_session)
    await db_session.flush()
    await _seed_provider_and_models(db_session, ws_id)

    # Стираем default и вставляем правило, явно разрешающее всё
    from sqlalchemy import delete

    await db_session.execute(delete(RoutingRule).where(RoutingRule.workspace_id == ws_id))
    await db_session.flush()

    permissive = RoutingRule(
        workspace_id=ws_id,
        order=0,
        is_default=True,
        is_terminal=True,
        to_models=["*"],
        allow_locality=["local", "external"],
        reason="allow-everything",
    )
    db_session.add(permissive)
    await db_session.flush()

    rules = await load_rules(db_session, ws_id)
    candidates = await load_candidate_models(db_session, ws_id)

    ctx = RouteContext(
        candidate_models=candidates,
        user_role_name="developer",
        corpus_data_class="К3",
    )
    decision = select_model(rules, ctx)
    assert decision.model.locality == "local"
    # Внешние модели не попали в fallback
    for m in decision.fallbacks:
        assert m.locality == "local"
