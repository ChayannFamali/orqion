"""Тесты CRUD правил маршрутизации (T-114a).

Проверки:
- admin (capabilities=["*"]) может CRUD
- developer (без manage_routing) → 403
- POST/PATCH/DELETE через реальный HTTP
- дубликат order → 409
- инвариант ADR-12: удаление seed-правила К2/К3 через DELETE /api/routing-rules/{id}
  → _filter_data_class продолжает блокировать external для К2/К3
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Role, RoutingRule, User
from app.policy.presets import BUILTIN_ROLES
from app.providers.client import ProviderClient
from fastapi import FastAPI
from sqlalchemy import select


async def _login_with_role(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    role_name: str = "admin",
    policy: dict[str, Any] | None = None,
) -> str:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role_policy = policy or BUILTIN_ROLES[role_name].model_dump()
        role = Role(
            workspace_id=workspace_id,
            name=role_name,
            is_builtin=True,
            policy=role_policy,
        )
        session.add(role)
        await session.flush()

        user = User(
            workspace_id=workspace_id,
            email=f"routing-{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _get_k2_rule_id(app_fixture: FastAPI) -> str:
    """Находит ID seed-правила К2/К3→local."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        result = await session.execute(
            select(RoutingRule).where(
                RoutingRule.workspace_id == workspace_id,
            ).order_by(RoutingRule.order)
        )
        for rule in result.scalars().all():
            if rule.when_corpus_class and "К2" in rule.when_corpus_class:
                return rule.id
    # Если не найдено seed-правило К2, ищем по allow_locality=["local"]
    async with factory() as session:
        result = await session.execute(
            select(RoutingRule).where(
                RoutingRule.workspace_id == workspace_id,
                RoutingRule.allow_locality.is_not(None),
            )
        )
        for rule in result.scalars().all():
            if rule.allow_locality == ["local"]:
                return rule.id
    pytest.fail("Seed rule К2/К3→local not found")


@pytest.mark.asyncio
async def test_admin_can_list_routing_rules(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin видит список seed-правил."""
    await _login_with_role(api_client, app_fixture, "admin")

    response = await api_client.get("/api/routing-rules")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all("id" in r and "order" in r for r in body["rules"])


@pytest.mark.asyncio
async def test_developer_cannot_access_routing_rules(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Developer (без manage_routing) → 403."""
    await _login_with_role(api_client, app_fixture, "developer")

    response = await api_client.get("/api/routing-rules")
    assert response.status_code == 403
    assert response.json()["error"] == "routing_permission_denied"


@pytest.mark.asyncio
async def test_admin_can_create_routing_rule(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin может создать новое правило."""
    await _login_with_role(api_client, app_fixture, "admin")

    response = await api_client.post(
        "/api/routing-rules",
        json={
            "order": 100,
            "is_default": False,
            "is_terminal": True,
            "to_models": ["local/test-model"],
            "reason": "test-rule",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["order"] == 100
    assert body["reason"] == "test-rule"
    assert body["id"] is not None


@pytest.mark.asyncio
async def test_duplicate_order_rejected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Дубликат order → 409."""
    await _login_with_role(api_client, app_fixture, "admin")

    # Сначала получаем существующие правила
    response = await api_client.get("/api/routing-rules")
    existing_order = response.json()["rules"][0]["order"]

    response = await api_client.post(
        "/api/routing-rules",
        json={
            "order": existing_order,
            "reason": "duplicate",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "duplicate_rule_order"


@pytest.mark.asyncio
async def test_admin_can_update_routing_rule(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin может обновить правило."""
    await _login_with_role(api_client, app_fixture, "admin")

    # Создаём правило
    create_response = await api_client.post(
        "/api/routing-rules",
        json={"order": 200, "reason": "before-update"},
    )
    rule_id = create_response.json()["id"]

    # Обновляем
    response = await api_client.patch(
        f"/api/routing-rules/{rule_id}",
        json={"reason": "after-update", "is_terminal": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reason"] == "after-update"
    assert body["is_terminal"] is True


@pytest.mark.asyncio
async def test_admin_can_delete_routing_rule(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """Admin может удалить правило."""
    await _login_with_role(api_client, app_fixture, "admin")

    create_response = await api_client.post(
        "/api/routing-rules",
        json={"order": 300, "reason": "to-delete"},
    )
    rule_id = create_response.json()["id"]

    response = await api_client.delete(f"/api/routing-rules/{rule_id}")
    assert response.status_code == 204

    # Проверяем, что правило удалено
    list_response = await api_client.get("/api/routing-rules")
    rule_ids = [r["id"] for r in list_response.json()["rules"]]
    assert rule_id not in rule_ids


@pytest.mark.asyncio
async def test_update_nonexistent_rule_404(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
) -> None:
    """PATCH несуществующего правила → 404."""
    await _login_with_role(api_client, app_fixture, "admin")

    response = await api_client.patch(
        "/api/routing-rules/nonexistent-id",
        json={"reason": "test"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_adr12_invariant_survives_seed_rule_deletion(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Инвариант ADR-12: удаление seed-правила К2/К3 через DELETE /api/routing-rules/{id}
    не отключает фильтр data_class. _filter_data_class — код, не данные.

    После удаления seed-правила К2/К3→local:
    - К3 корпус с external моделью → маршрутизация не выдаст external модель
    - Фильтр работает, потому что он в коде (_filter_data_class), а не в данных
    """
    await _login_with_role(api_client, app_fixture, "admin")

    # Создаём локальную и внешнюю модель
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

        local_model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias="local/k3-model",
            upstream_name="k3-model",
            locality="local",
            max_input_tokens=32000,
            enabled=True,
        )
        external_model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias="external/k3-model",
            upstream_name="k3-ext",
            locality="external",
            max_input_tokens=32000,
            enabled=True,
        )
        session.add(local_model)
        session.add(external_model)
        await session.commit()

    # Находим и удаляем seed-правило К2/К3→local через реальный HTTP
    k2_rule_id = await _get_k2_rule_id(app_fixture)
    delete_response = await api_client.delete(f"/api/routing-rules/{k2_rule_id}")
    assert delete_response.status_code == 204

    # Подменяем провайдер-заглушкой
    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": "response"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    # Отправляем чат-запрос с corpus_data_class=К3
    # После удаления seed-правила К2/К3→local, _filter_data_class (код, не данные)
    # всё равно блокирует external модели для К3
    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "test"}],
            "stream": False,
            "corpus_data_class": "К3",
        },
    )

    # Если _filter_data_class работает (инвариант ADR-12):
    # external модель отфильтрована, выбрана local
    # Если нет — external модель прошла, и это нарушение ADR-12
    if response.status_code == 200:
        body = response.json()
        assert body.get("model") == "local/k3-model"
    else:
        # Если маршрутизатор не нашёл моделей (нет local, есть только external
        # но фильтр их убрал) — это тоже правильное поведение
        assert response.status_code in (404, 503)
