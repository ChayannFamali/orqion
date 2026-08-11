"""Тесты переформулировки запроса (T-218).

Проверки:
- test_disabled_returns_original: флаг выключен → pass-through, degraded=False
- test_empty_alias_returns_original: alias="" → деградация, degraded=True
- test_no_history_returns_original: 1 сообщение → pass-through, degraded=False
- test_rewrite_calls_complete: complete вызывается с system + историей
- test_rewrite_returns_reformulated: возвращает content из ответа
- test_rewrite_cleans_quotes: кавычки по краям удаляются
- test_rewrite_empty_content_returns_original: пустой content → деградация
- test_rewrite_error_returns_original: complete бросает → деградация
- test_rewrite_model_not_found_returns_original: alias не в БД → деградация
- test_rewrite_model_disabled_returns_original: model.enabled=False → деградация
- test_rewrite_provider_disabled_returns_original: provider.enabled=False → деградация
- test_rewrite_trace_span_payload: span с correct payload
- test_rewrite_trace_span_payload_degraded: span при деградации
- test_rewrite_max_context_messages: длинная история → последние N сообщений
"""

from __future__ import annotations

from typing import Any

import pytest
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider, Workspace
from app.providers.client import ProviderClient
from app.rag.query_rewrite import maybe_rewrite_query
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

_WORKSPACE_ID = "ws-rewrite-1"


def _settings(
    enabled: bool = False,
    alias: str = "",
) -> Settings:
    return Settings(
        rag_query_reformulation_enabled=enabled,
        rag_reformulation_model_alias=alias,
    )


async def _seed_provider_and_model(
    session: AsyncSession,
    workspace_id: str,
    model_alias: str = "local/rewrite-model",
    model_enabled: bool = True,
    provider_enabled: bool = True,
    secret_key: str = "test-secret",
) -> tuple[Provider, Model]:
    provider = Provider(
        workspace_id=workspace_id,
        kind="openai",
        base_url="http://stub:1234/v1",
        api_key_enc=encrypt_api_key("sk-test", secret_key),
        enabled=provider_enabled,
        capabilities={},
    )
    session.add(provider)
    await session.flush()

    model = Model(
        workspace_id=workspace_id,
        provider_id=provider.id,
        alias=model_alias,
        upstream_name="rewrite-upstream",
        locality="local",
        enabled=model_enabled,
    )
    session.add(model)
    await session.flush()
    return provider, model


def _stub_complete(
    content: str,
) -> Any:
    """Возвращает async-заглушку для ProviderClient.complete."""
    response: dict[str, Any] = {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    async def _complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        return response

    return _complete


def _error_complete(
    exc: Exception,
) -> Any:
    """Возвращает async-заглушку, бросающую исключение."""

    async def _complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        raise exc

    return _complete


async def _make_workspace(session: AsyncSession) -> str:
    ws = Workspace(name="test")
    session.add(ws)
    await db_session_flush(session)
    return ws.id


async def db_session_flush(session: AsyncSession) -> None:
    await session.flush()


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


async def test_disabled_returns_original(db_session: AsyncSession) -> None:
    """Флаг выключен → pass-through, degraded=False."""
    workspace_id = await _make_workspace(db_session)
    settings = _settings(enabled=False, alias="local/rewrite-model")
    messages = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как его настроить?"
    assert result.degraded is False
    assert result.error is None


async def test_empty_alias_returns_original(db_session: AsyncSession) -> None:
    """alias="" → деградация, degraded=True."""
    workspace_id = await _make_workspace(db_session)
    settings = _settings(enabled=True, alias="")
    messages = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как его настроить?"
    assert result.degraded is True
    assert "not set" in (result.error or "")


async def test_no_history_returns_original(db_session: AsyncSession) -> None:
    """1 сообщение → pass-through, degraded=False."""
    workspace_id = await _make_workspace(db_session)
    settings = _settings(enabled=True, alias="local/rewrite-model")
    messages = [{"role": "user", "content": "Как настроить orqion?"}]

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как настроить orqion?"
    assert result.degraded is False
    assert result.error is None


async def test_rewrite_calls_complete(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """complete вызывается с system-промптом и историей."""
    workspace_id = await _make_workspace(db_session)
    await _seed_provider_and_model(db_session, workspace_id)
    settings = _settings(enabled=True, alias="local/rewrite-model")

    messages = [
        {"role": "user", "content": "Я установил orqion"},
        {"role": "assistant", "content": "Отлично"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    captured: list[dict[str, Any]] = []

    async def _capturing_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        captured.append(
            {
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return {
            "choices": [{"message": {"content": "Как настроить orqion?"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _capturing_complete)

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как настроить orqion?"
    assert result.degraded is False
    assert len(captured) == 1
    sent_messages = captured[0]["messages"]
    assert sent_messages[0]["role"] == "system"
    assert sent_messages[0]["content"]  # system-промпт непустой
    assert captured[0]["max_tokens"] == 512
    assert captured[0]["temperature"] == 0.0


async def test_rewrite_returns_reformulated(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Возвращает content из ответа модели."""
    workspace_id = await _make_workspace(db_session)
    await _seed_provider_and_model(db_session, workspace_id)
    settings = _settings(enabled=True, alias="local/rewrite-model")

    messages = [
        {"role": "user", "content": "Я установил orqion"},
        {"role": "assistant", "content": "Отлично"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete("Как настроить orqion?"))

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как настроить orqion?"
    assert result.degraded is False


async def test_rewrite_cleans_quotes() -> None:
    """Кавычки по краям ответа модели удаляются."""
    from app.rag.query_rewrite import _clean_response

    assert _clean_response('"Как настроить orqion?"') == "Как настроить orqion?"
    assert _clean_response("«Как настроить orqion?»") == "Как настроить orqion?"
    assert _clean_response("'Как настроить orqion?'") == "Как настроить orqion?"
    assert _clean_response("  Как настроить orqion?  ") == "Как настроить orqion?"
    assert _clean_response("Как настроить orqion?") == "Как настроить orqion?"


async def test_rewrite_empty_content_returns_original(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пустой content → деградация."""
    workspace_id = await _make_workspace(db_session)
    await _seed_provider_and_model(db_session, workspace_id)
    settings = _settings(enabled=True, alias="local/rewrite-model")

    messages = [
        {"role": "user", "content": "Я установил orqion"},
        {"role": "assistant", "content": "Отлично"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete(""))

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как его настроить?"
    assert result.degraded is True
    assert "empty response" in (result.error or "")


async def test_rewrite_error_returns_original(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """complete бросает → деградация."""
    workspace_id = await _make_workspace(db_session)
    await _seed_provider_and_model(db_session, workspace_id)
    settings = _settings(enabled=True, alias="local/rewrite-model")

    messages = [
        {"role": "user", "content": "Я установил orqion"},
        {"role": "assistant", "content": "Отлично"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    monkeypatch.setattr(ProviderClient, "complete", _error_complete(RuntimeError("provider down")))

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как его настроить?"
    assert result.degraded is True
    assert "provider down" in (result.error or "")


async def test_rewrite_model_not_found_returns_original(db_session: AsyncSession) -> None:
    """Alias не в БД → деградация."""
    workspace_id = await _make_workspace(db_session)
    settings = _settings(enabled=True, alias="local/nonexistent")

    messages = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как его настроить?"
    assert result.degraded is True
    assert "not found" in (result.error or "")


async def test_rewrite_model_disabled_returns_original(db_session: AsyncSession) -> None:
    """model.enabled=False → деградация."""
    workspace_id = await _make_workspace(db_session)
    await _seed_provider_and_model(db_session, workspace_id, model_enabled=False)
    settings = _settings(enabled=True, alias="local/rewrite-model")

    messages = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как его настроить?"
    assert result.degraded is True
    assert "not found" in (result.error or "")


async def test_rewrite_provider_disabled_returns_original(db_session: AsyncSession) -> None:
    """provider.enabled=False → деградация."""
    workspace_id = await _make_workspace(db_session)
    await _seed_provider_and_model(db_session, workspace_id, provider_enabled=False)
    settings = _settings(enabled=True, alias="local/rewrite-model")

    messages = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Как его настроить?"
    assert result.degraded is True
    assert "not found or disabled" in (result.error or "")


async def test_rewrite_trace_span_payload(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Span 'rewrite' с правильным payload."""
    from app.trace.service import create_trace

    workspace_id = await _make_workspace(db_session)
    await _seed_provider_and_model(db_session, workspace_id)
    settings = _settings(enabled=True, alias="local/rewrite-model")

    messages = [
        {"role": "user", "content": "Я установил orqion"},
        {"role": "assistant", "content": "Отлично"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    trace_ctx = await create_trace(db_session, workspace_id)

    monkeypatch.setattr(ProviderClient, "complete", _stub_complete("Как настроить orqion?"))

    result = await maybe_rewrite_query(
        db_session, settings, messages, "test-secret", workspace_id, trace_ctx
    )

    assert result.query == "Как настроить orqion?"
    assert result.degraded is False
    assert len(trace_ctx.spans) == 1
    span_rec = trace_ctx.spans[0]
    assert span_rec.name == "rewrite"
    assert span_rec.payload["original_query"] == "Как его настроить?"
    assert span_rec.payload["rewritten_query"] == "Как настроить orqion?"
    assert span_rec.payload["model_alias"] == "local/rewrite-model"
    assert span_rec.payload["degraded"] is False
    assert span_rec.payload["error"] is None


async def test_rewrite_trace_span_payload_degraded(db_session: AsyncSession) -> None:
    """Span 'rewrite' при деградации: degraded=True, error заполнен."""
    from app.trace.service import create_trace

    workspace_id = await _make_workspace(db_session)
    settings = _settings(enabled=True, alias="local/nonexistent")

    messages = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуйте"},
        {"role": "user", "content": "Как его настроить?"},
    ]

    trace_ctx = await create_trace(db_session, workspace_id)

    result = await maybe_rewrite_query(
        db_session, settings, messages, "test-secret", workspace_id, trace_ctx
    )

    assert result.degraded is True
    assert len(trace_ctx.spans) == 1
    span_rec = trace_ctx.spans[0]
    assert span_rec.payload["degraded"] is True
    error_val = str(span_rec.payload["error"])
    assert "not found" in error_val
    assert span_rec.payload["rewritten_query"] == "Как его настроить?"


async def test_rewrite_max_context_messages(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Длинная история → в complete передаются последние N сообщений."""
    workspace_id = await _make_workspace(db_session)
    await _seed_provider_and_model(db_session, workspace_id)
    settings = _settings(enabled=True, alias="local/rewrite-model")

    # 15 сообщений + system = 16 в complete, но история обрезается до 10
    messages: list[dict[str, str]] = []
    for i in range(7):
        messages.append({"role": "user", "content": f"Вопрос {i}"})
        messages.append({"role": "assistant", "content": f"Ответ {i}"})
    messages.append({"role": "user", "content": "Последний вопрос"})

    captured: list[list[dict[str, str]]] = []

    async def _capturing_complete(
        self: ProviderClient,
        messages: list[dict[str, str]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        captured.append(messages)
        return {
            "choices": [
                {
                    "message": {"content": "Переформулированный последний вопрос"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(ProviderClient, "complete", _capturing_complete)

    result = await maybe_rewrite_query(db_session, settings, messages, "test-secret", workspace_id)

    assert result.query == "Переформулированный последний вопрос"
    # system + max 10 history = 11
    assert len(captured[0]) == 11
    assert captured[0][0]["role"] == "system"
