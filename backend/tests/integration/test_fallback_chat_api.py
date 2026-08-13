"""Тесты fallback при ошибке провайдера (T-116b).

Проверки:
- основная модель 5xx → fallback → успех → usage_event записан с fallback-моделью
- support role: fallback не уходит на external (policy.models)
- ошибка после первого токена → error event, без fallback
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.auth.passwords import hash_password
from app.auth.sessions import COOKIE_NAME, create_session
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import AuditLog, Model, Provider, Role, RoutingRule, User
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
            email=f"{role_name}@orqion.local",
            password_hash=hash_password("pass-123"),
            role_id=role.id,
        )
        session.add(user)
        await session.flush()

        session_id = await create_session(session, user.id, workspace_id, Settings())
        await session.commit()

    api_client.cookies.set(COOKIE_NAME, session_id)
    return user.id


async def _seed_provider_and_model(
    app_fixture: FastAPI,
    alias: str,
    upstream: str,
    locality: str = "local",
    enabled: bool = True,
) -> tuple[str, str]:
    """Создаёт провайдера и модель. Возвращает (model_id, provider_id)."""
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

        model = Model(
            workspace_id=workspace_id,
            provider_id=provider.id,
            alias=alias,
            upstream_name=upstream,
            locality=locality,
            max_input_tokens=32000,
            enabled=enabled,
        )
        session.add(model)
        await session.commit()
        return model.id, provider.id


async def _seed_routing_rule_with_fallback(
    app_fixture: FastAPI,
    primary_alias: str,
    fallback_alias: str,
) -> None:
    """Создаёт routing rule: to=[primary], fallback=[fallback]."""
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        rule = RoutingRule(
            workspace_id=workspace_id,
            order=1,
            is_default=False,
            is_terminal=True,
            when_corpus_class=None,
            when_role=None,
            when_task=None,
            when_model_alias=None,
            to_models=[primary_alias],
            allow_locality=None,
            fallback_models=[fallback_alias],
            reason="test-fallback-rule",
        )
        session.add(rule)
        # Удаляем default rule, чтобы наша сработала
        result = await session.execute(
            select(RoutingRule).where(
                RoutingRule.workspace_id == workspace_id,
                RoutingRule.is_default.is_(True),
            )
        )
        for r in result.scalars().all():
            await session.delete(r)
        await session.commit()


@pytest.mark.asyncio
async def test_fallback_on_primary_5xx_non_stream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Основная модель 5xx → fallback → успех → usage_event записан с fallback-моделью."""
    await _login_with_role(api_client, app_fixture, "admin")
    primary_id, _ = await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream")
    fallback_id, _ = await _seed_provider_and_model(
        app_fixture, "local/fallback", "fallback-upstream"
    )
    await _seed_routing_rule_with_fallback(app_fixture, "local/primary", "local/fallback")

    call_count = {"primary": 0, "fallback": 0}

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        if model == "primary-upstream":
            call_count["primary"] += 1
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        call_count["fallback"] += 1
        return {
            "choices": [{"message": {"content": "Fallback response"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "complete"
    assert body["content"] == "Fallback response"
    assert body["model"] == "local/fallback"

    assert call_count["primary"] >= 1
    assert call_count["fallback"] == 1

    # Проверяем, что usage_event записан с fallback-моделью
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    from app.db.models import UsageEvent

    async with factory() as session:
        result = await session.execute(
            select(UsageEvent).where(UsageEvent.workspace_id == workspace_id)
        )
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].model_id == fallback_id
        assert events[0].model_id != primary_id


@pytest.mark.asyncio
async def test_fallback_on_primary_5xx_stream(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stream: основная модель 5xx до первого токена → fallback → стриминг успешно."""
    await _login_with_role(api_client, app_fixture, "admin")
    await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream")
    fallback_id, _ = await _seed_provider_and_model(
        app_fixture, "local/fallback", "fallback-upstream"
    )
    await _seed_routing_rule_with_fallback(app_fixture, "local/primary", "local/fallback")

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        if model == "primary-upstream":
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        yield "Fallback"
        yield " "
        yield "token"

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    lines = response.text.strip().split("\n")
    token_events = [
        json.loads(l[6:]) for l in lines if l.startswith("data: ") and "[DONE]" not in l
    ]
    token_events = [e for e in token_events if e["type"] == "token"]
    assert len(token_events) == 3
    assert "".join(e["v"] for e in token_events) == "Fallback token"

    # Проверяем, что usage_event записан с fallback-моделью
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    from app.db.models import UsageEvent

    async with factory() as session:
        result = await session.execute(
            select(UsageEvent).where(UsageEvent.workspace_id == workspace_id)
        )
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].model_id == fallback_id


@pytest.mark.asyncio
async def test_no_fallback_after_first_token(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ошибка после первого токена → error event, без fallback."""
    await _login_with_role(api_client, app_fixture, "admin")
    await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream")
    await _seed_provider_and_model(app_fixture, "local/fallback", "fallback-upstream")
    await _seed_routing_rule_with_fallback(app_fixture, "local/primary", "local/fallback")

    async def _stub_stream(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> Any:
        if model == "primary-upstream":
            yield "First"
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        # Fallback не должен вызываться
        yield "Should not see this"

    monkeypatch.setattr(ProviderClient, "stream", _stub_stream)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    lines = response.text.strip().split("\n")
    events = [json.loads(l[6:]) for l in lines if l.startswith("data: ") and "[DONE]" not in l]
    token_events = [e for e in events if e["type"] == "token"]
    error_events = [e for e in events if e["type"] == "error"]

    # Был один токен, затем ошибка — fallback не применился
    assert len(token_events) == 1
    assert token_events[0]["v"] == "First"
    assert len(error_events) == 1
    assert error_events[0]["code"] == "provider_unavailable"


@pytest.mark.asyncio
async def test_support_role_fallback_stays_local(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Support role (models=["local/*"]): fallback не уходит на external."""
    support_policy = BUILTIN_ROLES["support"].model_dump()
    await _login_with_role(api_client, app_fixture, "support", policy=support_policy)

    await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream", "local")
    await _seed_provider_and_model(
        app_fixture, "external/fallback", "external-upstream", "external"
    )
    await _seed_routing_rule_with_fallback(app_fixture, "local/primary", "external/fallback")

    fallback_called = {"yes": False}

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        if model == "primary-upstream":
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        fallback_called["yes"] = True
        return {
            "choices": [{"message": {"content": "External"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    # Support не может использовать external-модель: fallback отфильтрован policy.models
    # Primary падает, fallback недоступен → ошибка
    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )

    # Маршрутизатор: fallback external/fallback отфильтрован через policy.models
    # Primary падает, fallback недоступен → ошибка
    body = response.json()
    assert body["type"] == "error"
    assert fallback_called["yes"] is False


# ---------------------------------------------------------------------------
# T-402: ADR-12 hard channel binding — admin cannot bypass, fallback stays local
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_k3_fallback_all_local(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin + К3 корпус + primary local падает → fallback local вызывается, external НЕ вызывается."""
    await _login_with_role(api_client, app_fixture, "admin")

    await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream", "local")
    await _seed_provider_and_model(app_fixture, "local/fallback", "fallback-upstream", "local")
    await _seed_provider_and_model(app_fixture, "external/evil", "evil-upstream", "external")

    # Routing rule: primary=local, fallback=[local, external]
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        # Delete ALL existing rules, not just defaults
        result = await session.execute(
            select(RoutingRule).where(RoutingRule.workspace_id == workspace_id)
        )
        for r in result.scalars().all():
            await session.delete(r)

        rule = RoutingRule(
            workspace_id=workspace_id,
            order=1,
            is_default=False,
            is_terminal=True,
            when_corpus_class=None,
            when_role=None,
            when_task=None,
            when_model_alias=None,
            to_models=["local/primary"],
            allow_locality=None,
            fallback_models=["local/fallback", "external/evil"],
            reason="test-k3-fallback",
        )
        session.add(rule)
        await session.commit()

    call_log: list[str] = []

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        call_log.append(model)
        if model == "primary-upstream":
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        return {
            "choices": [{"message": {"content": "Local fallback OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "secret"}],
            "corpus_data_class": "К3",
            "stream": False,
        },
    )

    assert response.status_code == 200, f"Status: {response.status_code}, body: {response.json()}"
    body = response.json()
    assert body.get("type") == "complete", f"Unexpected body: {body}, call_log: {call_log}"
    assert body["model"] == "local/fallback"
    assert "evil-upstream" not in call_log
    assert "fallback-upstream" in call_log


@pytest.mark.asyncio
async def test_admin_k3_external_fallback_blocked(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin + К3 корпус + routing rule с external fallback → primary падает,
    external fallback отфильтрован, ошибка (нет доступных fallback)."""
    await _login_with_role(api_client, app_fixture, "admin")

    await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream", "local")
    await _seed_provider_and_model(app_fixture, "external/evil", "evil-upstream", "external")

    # Routing rule: primary=local, fallback=[external only]
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        # Delete ALL existing rules, not just defaults
        result = await session.execute(
            select(RoutingRule).where(RoutingRule.workspace_id == workspace_id)
        )
        for r in result.scalars().all():
            await session.delete(r)

        rule = RoutingRule(
            workspace_id=workspace_id,
            order=1,
            is_default=False,
            is_terminal=True,
            when_corpus_class=None,
            when_role=None,
            when_task=None,
            when_model_alias=None,
            to_models=["local/primary"],
            allow_locality=None,
            fallback_models=["external/evil"],
            reason="test-k3-external-fallback",
        )
        session.add(rule)
        await session.commit()

    external_called = {"yes": False}

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        if model == "primary-upstream":
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        external_called["yes"] = True
        return {
            "choices": [{"message": {"content": "External leak"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "secret"}],
            "corpus_data_class": "К3",
            "stream": False,
        },
    )

    # Primary упал, external fallback отфильтрован _filter_data_class → ошибка в body
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "error"
    assert body["code"] == "provider_unavailable"
    assert external_called["yes"] is False
    # T-008/§7.3: ошибка содержит reason/constraint/hint, не generic отказ
    assert body["reason"] is not None
    assert body["hint"] is not None


@pytest.mark.asyncio
async def test_admin_k2_pinned_model_fallback_stays_local(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin + К2 + primary local падает → fallback локальный, external НЕ вызывается.

    T-402: fallback chain для К2 состоит только из локальных моделей.
    Использует corpus_data_class напрямую (pinned_model_id тестируется в test_rag_chat_api).
    """
    await _login_with_role(api_client, app_fixture, "admin")

    await _seed_provider_and_model(app_fixture, "local/primary", "primary-upstream", "local")
    await _seed_provider_and_model(app_fixture, "local/fallback2", "fallback2-upstream", "local")
    await _seed_provider_and_model(app_fixture, "external/evil2", "evil2-upstream", "external")

    # Routing rule: primary=local, fallback=[local, external]
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        # Delete ALL existing rules
        result = await session.execute(
            select(RoutingRule).where(RoutingRule.workspace_id == workspace_id)
        )
        for r in result.scalars().all():
            await session.delete(r)

        rule = RoutingRule(
            workspace_id=workspace_id,
            order=1,
            is_default=False,
            is_terminal=True,
            when_corpus_class=None,
            when_role=None,
            when_task=None,
            when_model_alias=None,
            to_models=["local/primary"],
            allow_locality=None,
            fallback_models=["local/fallback2", "external/evil2"],
            reason="test-k2-fallback",
        )
        session.add(rule)
        await session.commit()

    call_log: list[str] = []

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        call_log.append(model)
        if model == "primary-upstream":
            raise httpx.HTTPStatusError(
                "Internal Server Error",
                request=httpx.Request("POST", "http://stub/v1/chat/completions"),
                response=httpx.Response(500),
            )
        return {
            "choices": [{"message": {"content": "Local fallback OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "secret"}],
            "corpus_data_class": "К2",
            "stream": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body.get("type") == "complete", f"Unexpected body: {body}, call_log: {call_log}"
    assert body["model"] == "local/fallback2"
    assert "evil2-upstream" not in call_log
    assert "fallback2-upstream" in call_log


@pytest.mark.asyncio
async def test_admin_k3_no_pinned_model_explicit_external_alias_rejected(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin + К3 корпус + pinned_model_id=None + явный model_alias=external →
    DataClassViolation (403), не проходит.

    Сценарий из T-401: pinned_model_id может быть null при К2/К3.
    Защита ложится на enforce() → DataClassViolation (шаг 1, до видимости модели).
    """
    await _login_with_role(api_client, app_fixture, "admin")

    from app.db.models import Corpus, IndexVersion

    await _seed_provider_and_model(app_fixture, "external/gpt-4", "gpt-4-upstream", "external")

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        corpus = Corpus(
            workspace_id=workspace_id,
            name="k3-no-pinned",
            data_class="К3",
            pinned_model_id=None,
        )
        session.add(corpus)
        await session.flush()

        iv = IndexVersion(
            workspace_id=workspace_id,
            corpus_id=corpus.id,
            embedding_model="BAAI/bge-m3",
            chunker="mixed-v1",
            chunker_version="1",
            status="active",
        )
        session.add(iv)
        await session.flush()
        corpus.active_index_version_id = iv.id
        await session.commit()

    external_called = {"yes": False}

    async def _stub_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        external_called["yes"] = True
        return {
            "choices": [{"message": {"content": "leaked"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "secret"}],
            "corpus_name": "k3-no-pinned",
            "model_alias": "external/gpt-4",
            "stream": False,
        },
    )

    # _filter_data_class убирает external из candidates до routing (К3 + external → filtered)
    # Затем enforce() проверяет выбранную модель (local) — проходит
    # Но если нет local моделей → NoRouteAvailable (503)
    # Если есть local модели → выбрана local, external не вызвана
    # В любом случае external модель НЕ вызвана
    assert response.status_code == 503
    assert external_called["yes"] is False
    body = response.json()
    assert body["error"] == "no_route_available"
    assert body["reason"] is not None
    assert body["constraint"] is not None
    assert body["constraint"]["data_class"] == "\u041a3"
    assert body["hint"] is not None
    assert "\u043b\u043e\u043a\u0430\u043b\u044c\u043d\u044b\u0435" in body["hint"]


@pytest.mark.asyncio
async def test_data_class_violation_logged_in_audit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-403: DataClassViolation (ADR-12 bypass attempt) logged in audit_log."""
    await _login_with_role(api_client, app_fixture, "admin")

    await _seed_provider_and_model(app_fixture, "external/gpt-4", "gpt-4-upstream", "external")

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "secret"}],
            "corpus_data_class": "\u041a3",
            "model_alias": "external/gpt-4",
            "stream": False,
        },
    )

    # Request rejected (503 NoRouteAvailable — _filter_data_class removes external)
    assert response.status_code == 503

    # Audit log contains security.data_class_violation
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        from sqlalchemy import select as sa_select

        result = await session.execute(
            sa_select(AuditLog).where(
                AuditLog.workspace_id == ws_id,
                AuditLog.action == "security.data_class_violation",
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.meta["error"] == "no_route_available"
        assert audit.meta["constraint"]["data_class"] == "\u041a3"
        assert audit.meta["model_alias"] == "external/gpt-4"


@pytest.mark.asyncio
async def test_data_class_violation_enforce_path_logged_in_audit(
    api_client: httpx.AsyncClient,
    app_fixture: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-403: DataClassViolation from enforce() (second layer) also logged in audit.

    Covers the DataClassViolation branch of _is_adr12_violation() — distinct from
    the NoRouteAvailable branch tested above. enforce() is the second layer of ADR-12
    defence, triggered when _filter_data_class somehow lets an external model through.
    """
    await _login_with_role(api_client, app_fixture, "admin")

    await _seed_provider_and_model(app_fixture, "local/test", "test-upstream", "local")

    # Mock enforce_all to raise DataClassViolation (simulates second-layer defence)
    from app.chat import service as chat_service
    from app.errors import DataClassViolation

    async def _mock_enforce_all(*args: object, **kwargs: object) -> None:
        raise DataClassViolation(
            constraint={"data_class": "\u041a3", "model_locality": "external"},
            hint="\u041a3 requires local-only models",
        )

    monkeypatch.setattr(chat_service, "enforce_all", _mock_enforce_all)

    response = await api_client.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "secret"}],
            "corpus_data_class": "\u041a3",
            "stream": False,
        },
    )

    # DataClassViolation → 403
    assert response.status_code == 403

    # Audit log contains security.data_class_violation with error=data_class_violation
    factory = app_fixture.state.db_session_factory
    ws_id = app_fixture.state.workspace_id
    async with factory() as session:
        from sqlalchemy import select as sa_select

        result = await session.execute(
            sa_select(AuditLog).where(
                AuditLog.workspace_id == ws_id,
                AuditLog.action == "security.data_class_violation",
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.meta["error"] == "data_class_violation"
        assert audit.meta["constraint"]["data_class"] == "\u041a3"
