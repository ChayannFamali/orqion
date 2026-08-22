"""Тесты генерации заголовка (T-433).

Проверки (по аналогии с test_query_rewrite.py):
- test_disabled_returns_fallback: флаг выключен → fallback, degraded=False
- test_empty_alias_returns_fallback: alias="" → fallback, degraded=True
- test_generate_calls_complete: complete вызывается с system + обменом
- test_generate_returns_title: возвращает content из ответа
- test_generate_cleans_quotes: кавычки по краям удаляются
- test_generate_truncates_long_title: длинный заголовок обрезается по слову
- test_generate_empty_content_returns_fallback: пустой content → fallback
- test_generate_error_returns_fallback: complete бросает → fallback
- test_generate_model_not_found_returns_fallback: alias не в БД → fallback
- test_generate_model_disabled_returns_fallback: model.enabled=False → fallback
- test_generate_provider_disabled_returns_fallback: provider.enabled=False → fallback
- test_utility_alias_fallback_to_rag_reformulation: utility_model_alias пуст,
  rag_reformulation_model_alias задан → используется rag_reformulation_model_alias
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import Model, Provider
from app.rag.title_generation import maybe_generate_title
from sqlalchemy.ext.asyncio import AsyncSession

_WORKSPACE_ID = "ws-title-1"


def _settings(
    enabled: bool = False,
    utility_alias: str = "",
    rag_alias: str = "",
) -> Settings:
    s = Settings()
    s.title_generation_enabled = enabled
    s.utility_model_alias = utility_alias
    s.rag_reformulation_model_alias = rag_alias
    return s


def _make_provider_row(
    workspace_id: str = _WORKSPACE_ID,
    kind: str = "openai",
    enabled: bool = True,
) -> Provider:
    return Provider(
        workspace_id=workspace_id,
        kind=kind,
        base_url="http://test-provider",
        api_key_enc=encrypt_api_key("sk-test", "test-secret-key-32-bytes-long!!"),
        enabled=enabled,
        capabilities={},
    )


def _make_model_row(
    provider_id: str,
    alias: str = "utility-1",
    upstream_name: str = "gpt-4o-mini",
    enabled: bool = True,
    workspace_id: str = _WORKSPACE_ID,
) -> Model:
    return Model(
        workspace_id=workspace_id,
        provider_id=provider_id,
        alias=alias,
        upstream_name=upstream_name,
        locality="external",
        max_input_tokens=128_000,
        enabled=enabled,
    )


def _mock_session(
    model: Model | None,
    provider: Provider | None,
) -> MagicMock:
    """Мок AsyncSession с select(Model) → model, select(Provider) → provider."""
    session = MagicMock(spec=AsyncSession)
    model_result = MagicMock()
    model_result.scalar_one_or_none.return_value = model
    provider_result = MagicMock()
    provider_result.scalar_one_or_none.return_value = provider

    call_count = 0

    async def _execute(stmt: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return model_result
        return provider_result

    session.execute = AsyncMock(side_effect=_execute)
    return session


@pytest.mark.asyncio
async def test_disabled_returns_fallback() -> None:
    settings = _settings(enabled=False)
    session = _mock_session(None, None)
    result = await maybe_generate_title(
        session, settings, "Hello", "Hi there!", "secret", _WORKSPACE_ID
    )
    assert result.title == "Hello"
    assert result.degraded is False


@pytest.mark.asyncio
async def test_empty_alias_returns_fallback() -> None:
    settings = _settings(enabled=True, utility_alias="", rag_alias="")
    session = _mock_session(None, None)
    result = await maybe_generate_title(
        session, settings, "Hello", "Hi there!", "secret", _WORKSPACE_ID
    )
    assert result.title == "Hello"
    assert result.degraded is True
    assert "not set" in (result.error or "")


@pytest.mark.asyncio
async def test_generate_calls_complete() -> None:
    settings = _settings(enabled=True, utility_alias="utility-1")
    provider = _make_provider_row()
    model = _make_model_row(provider_id=provider.id, alias="utility-1")
    session = _mock_session(model, provider)

    complete_response = {"choices": [{"message": {"content": "Generated Title"}}]}
    with patch("app.rag.title_generation.ProviderClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value=complete_response)
        mock_client_cls.return_value = mock_client

        result = await maybe_generate_title(
            session, settings, "Hello world", "Hi there!", "secret", _WORKSPACE_ID
        )

    assert result.title == "Generated Title"
    mock_client.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_returns_title() -> None:
    settings = _settings(enabled=True, utility_alias="utility-1")
    provider = _make_provider_row()
    model = _make_model_row(provider_id=provider.id, alias="utility-1")
    session = _mock_session(model, provider)

    with patch("app.rag.title_generation.ProviderClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(
            return_value={"choices": [{"message": {"content": "My Dialog"}}]}
        )
        mock_client_cls.return_value = mock_client

        result = await maybe_generate_title(
            session, settings, "Hello", "Hi!", "secret", _WORKSPACE_ID
        )

    assert result.title == "My Dialog"


@pytest.mark.asyncio
async def test_generate_cleans_quotes() -> None:
    settings = _settings(enabled=True, utility_alias="utility-1")
    provider = _make_provider_row()
    model = _make_model_row(provider_id=provider.id, alias="utility-1")
    session = _mock_session(model, provider)

    with patch("app.rag.title_generation.ProviderClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(
            return_value={"choices": [{"message": {"content": '"Quoted Title"'}}]}
        )
        mock_client_cls.return_value = mock_client

        result = await maybe_generate_title(
            session, settings, "Hello", "Hi!", "secret", _WORKSPACE_ID
        )

    assert result.title == "Quoted Title"


@pytest.mark.asyncio
async def test_generate_truncates_long_title() -> None:
    settings = _settings(enabled=True, utility_alias="utility-1")
    provider = _make_provider_row()
    model = _make_model_row(provider_id=provider.id, alias="utility-1")
    session = _mock_session(model, provider)

    long_title = "A" * 120
    with patch("app.rag.title_generation.ProviderClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(
            return_value={"choices": [{"message": {"content": long_title}}]}
        )
        mock_client_cls.return_value = mock_client

        result = await maybe_generate_title(
            session, settings, "Hello", "Hi!", "secret", _WORKSPACE_ID
        )

    assert len(result.title) <= 80


@pytest.mark.asyncio
async def test_generate_empty_content_returns_fallback() -> None:
    settings = _settings(enabled=True, utility_alias="utility-1")
    provider = _make_provider_row()
    model = _make_model_row(provider_id=provider.id, alias="utility-1")
    session = _mock_session(model, provider)

    with patch("app.rag.title_generation.ProviderClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(return_value={"choices": [{"message": {"content": ""}}]})
        mock_client_cls.return_value = mock_client

        result = await maybe_generate_title(
            session, settings, "Hello", "Hi!", "secret", _WORKSPACE_ID
        )

    assert result.title == "Hello"
    assert result.degraded is True
    assert "empty" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_generate_error_returns_fallback() -> None:
    settings = _settings(enabled=True, utility_alias="utility-1")
    provider = _make_provider_row()
    model = _make_model_row(provider_id=provider.id, alias="utility-1")
    session = _mock_session(model, provider)

    with patch("app.rag.title_generation.ProviderClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(side_effect=RuntimeError("Connection refused"))
        mock_client_cls.return_value = mock_client

        result = await maybe_generate_title(
            session, settings, "Hello", "Hi!", "secret", _WORKSPACE_ID
        )

    assert result.title == "Hello"
    assert result.degraded is True


@pytest.mark.asyncio
async def test_generate_model_not_found_returns_fallback() -> None:
    settings = _settings(enabled=True, utility_alias="missing-alias")
    session = _mock_session(None, None)

    result = await maybe_generate_title(session, settings, "Hello", "Hi!", "secret", _WORKSPACE_ID)

    assert result.title == "Hello"
    assert result.degraded is True
    assert "not found" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_generate_model_disabled_returns_fallback() -> None:
    settings = _settings(enabled=True, utility_alias="utility-1")
    provider = _make_provider_row()
    model = _make_model_row(provider_id=provider.id, alias="utility-1", enabled=False)
    session = _mock_session(model, provider)

    result = await maybe_generate_title(session, settings, "Hello", "Hi!", "secret", _WORKSPACE_ID)

    assert result.title == "Hello"
    assert result.degraded is True


@pytest.mark.asyncio
async def test_generate_provider_disabled_returns_fallback() -> None:
    settings = _settings(enabled=True, utility_alias="utility-1")
    provider = _make_provider_row(enabled=False)
    model = _make_model_row(provider_id=provider.id, alias="utility-1")
    session = _mock_session(model, provider)

    result = await maybe_generate_title(session, settings, "Hello", "Hi!", "secret", _WORKSPACE_ID)

    assert result.title == "Hello"
    assert result.degraded is True


@pytest.mark.asyncio
async def test_utility_alias_fallback_to_rag_reformulation() -> None:
    """utility_model_alias пуст, rag_reformulation_model_alias задан → используется rag_alias."""
    settings = _settings(enabled=True, utility_alias="", rag_alias="rag-utility")
    provider = _make_provider_row()
    model = _make_model_row(provider_id=provider.id, alias="rag-utility")
    session = _mock_session(model, provider)

    with patch("app.rag.title_generation.ProviderClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.complete = AsyncMock(
            return_value={"choices": [{"message": {"content": "Fallback Title"}}]}
        )
        mock_client_cls.return_value = mock_client

        result = await maybe_generate_title(
            session, settings, "Hello", "Hi!", "secret", _WORKSPACE_ID
        )

    assert result.title == "Fallback Title"
    assert result.degraded is False
