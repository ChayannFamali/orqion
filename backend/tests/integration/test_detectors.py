"""T-409: DLP-детекторы как плагины (ADR-13).

Тестируют:
- detectors_enabled=False (default) → детекторы не запускаются
- срабатывание → audit_log (fact, user, model, hash, patterns), без содержимого
- не сработал → нет записи в audit_log
- provider.kind=="local" → детекторы не запускаются
- сканируется финальный messages list (RAG-фрагменты), не только body.content
- primary=local → fallback=external → детектор запускается для fallback
- в audit_log — хеш, не содержимое
- пользовательский детектор через register_detector()
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from app.config import Settings
from app.crypto.service import encrypt_api_key
from app.db.models import (
    AuditLog,
    Model,
    Provider,
    Role,
    RoutingRule,
    User,
)
from app.detectors.protocol import DetectorResult
from app.detectors.registry import clear_detectors, register_detector
from fastapi import FastAPI
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeDetector:
    """Тестовый детектор: срабатывает если текст содержит 'SECRET'."""

    name = "test_secret_detector"

    def detect(self, text: str) -> DetectorResult:
        if "SECRET" in text:
            return DetectorResult(
                triggered=True,
                detector_type="secrets",
                matched_count=1,
                matched_patterns=["keyword:SECRET"],
            )
        return DetectorResult(
            triggered=False,
            detector_type="secrets",
            matched_count=0,
            matched_patterns=[],
        )


async def _seed_provider_and_model(
    app_fixture: FastAPI,
    alias: str,
    upstream: str,
    locality: str = "local",
    kind: str = "openai",
) -> tuple[str, str]:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        provider = Provider(
            workspace_id=workspace_id,
            kind=kind,
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
            enabled=True,
        )
        session.add(model)
        await session.commit()
        return model.id, provider.id


async def _seed_routing_rule(
    app_fixture: FastAPI,
    primary_alias: str,
    fallback_alias: str | None = None,
) -> None:
    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        _primary_id, _ = await _seed_provider_and_model(
            app_fixture, primary_alias, f"{primary_alias}-up", "local"
        )
        if fallback_alias:
            _fallback_id, _ = await _seed_provider_and_model(
                app_fixture, fallback_alias, f"{fallback_alias}-up", "external"
            )
        rule = RoutingRule(
            workspace_id=workspace_id,
            order=1,
            is_default=True,
            is_terminal=True,
            when_corpus_class=None,
            when_role=None,
            when_task=None,
            when_model_alias=None,
            to=[primary_alias],
            fallback=[fallback_alias] if fallback_alias else [],
        )
        session.add(rule)
        await session.commit()


async def _login_and_get_session_cookie(api_client: Any, app_fixture: FastAPI) -> str:
    """Логинится как admin и возвращает session cookie."""
    from app.auth.bootstrap import ensure_builtin_roles

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        await ensure_builtin_roles(session, workspace_id)
        await session.commit()

    resp = await api_client.post(
        "/api/auth/login",
        json={"email": "admin@orqion.local", "password": "admin"},
    )
    assert resp.status_code == 200
    cookie: str = resp.cookies.get("orqion_session", "")
    return cookie


@pytest.fixture(autouse=True)
def _clear_detectors() -> Generator[None]:
    """Очищает реестр детекторов после каждого теста."""
    yield
    clear_detectors()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detectors_disabled_by_default(app_fixture: FastAPI) -> None:
    """detectors_enabled=False (default) → детекторы не запускаются."""
    register_detector(_FakeDetector())

    # Проверяем что setting по умолчанию выключен
    settings = Settings()
    assert settings.detectors_enabled is False

    # run_detectors должен быть no-op
    from app.detectors.service import run_detectors

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        from app.db.models import User

        role = Role(workspace_id=workspace_id, name="admin", policy={}, is_builtin=True)
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email="test@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()

        # detectors_enabled=False — no-op
        await run_detectors(
            session,
            Settings(),
            user,
            "model-123",
            "conv-123",
            [{"role": "user", "content": "SECRET API_KEY=sk-12345"}],
            "external",
        )
        await session.commit()

    # Нет audit_log записи
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "security.detector_triggered")
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_detector_triggered_logs_audit(app_fixture: FastAPI) -> None:
    """Срабатывание → audit_log (fact, user, model, hash, patterns), без содержимого."""
    register_detector(_FakeDetector())

    from app.detectors.service import run_detectors

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(workspace_id=workspace_id, name="admin", policy={}, is_builtin=True)
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email="test@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()
        user_id = user.id

        settings = Settings(detectors_enabled=True)
        await run_detectors(
            session,
            settings,
            user,
            "model-456",
            "conv-456",
            [{"role": "user", "content": "I have a SECRET in my message"}],
            "external",
        )
        await session.commit()

    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "security.detector_triggered",
                AuditLog.actor_user_id == user_id,
            )
        )
        audit = result.scalar_one_or_none()
        assert audit is not None
        assert audit.object_type == "conversation"
        assert audit.object_id == "conv-456"
        assert audit.meta["detector_types"] == ["secrets"]
        assert audit.meta["detector_names"] == ["test_secret_detector"]
        assert audit.meta["model_id"] == "model-456"
        assert "request_hash" in audit.meta
        assert len(audit.meta["request_hash"]) == 16


@pytest.mark.asyncio
async def test_detector_not_triggered_no_log(app_fixture: FastAPI) -> None:
    """Не сработал → нет записи в audit_log."""
    register_detector(_FakeDetector())

    from app.detectors.service import run_detectors

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(workspace_id=workspace_id, name="admin", policy={}, is_builtin=True)
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email="clean@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()

        settings = Settings(detectors_enabled=True)
        await run_detectors(
            session,
            settings,
            user,
            "model-789",
            "conv-789",
            [{"role": "user", "content": "clean message without secrets"}],
            "external",
        )
        await session.commit()

    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "security.detector_triggered")
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_detectors_skip_local_provider(app_fixture: FastAPI) -> None:
    """provider.kind=="local" → детекторы не запускаются."""
    register_detector(_FakeDetector())

    from app.detectors.service import run_detectors

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(workspace_id=workspace_id, name="admin", policy={}, is_builtin=True)
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email="local@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()

        settings = Settings(detectors_enabled=True)
        await run_detectors(
            session,
            settings,
            user,
            "model-local",
            "conv-local",
            [{"role": "user", "content": "SECRET everywhere"}],
            "local",  # local provider — detectors skip
        )
        await session.commit()

    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "security.detector_triggered")
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_request_hash_not_content_in_log(app_fixture: FastAPI) -> None:
    """В audit_log meta — хеш, не содержимое текста."""
    register_detector(_FakeDetector())

    from app.detectors.service import run_detectors

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    secret_content = "My API KEY is SECRET sk-abc123"

    async with factory() as session:
        role = Role(workspace_id=workspace_id, name="admin", policy={}, is_builtin=True)
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email="hash@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()

        settings = Settings(detectors_enabled=True)
        await run_detectors(
            session,
            settings,
            user,
            "model-hash",
            "conv-hash",
            [{"role": "user", "content": secret_content}],
            "external",
        )
        await session.commit()

    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "security.detector_triggered")
        )
        audit = result.scalar_one()
        meta_str = str(audit.meta)
        # Содержимое НЕ должно быть в логе
        assert "sk-abc123" not in meta_str
        assert "API KEY" not in meta_str
        # Хеш должен быть
        assert "request_hash" in audit.meta
        assert len(audit.meta["request_hash"]) == 16


@pytest.mark.asyncio
async def test_custom_detector_registered() -> None:
    """Пользовательский детектор через register_detector()."""

    class CustomDetector:
        name = "custom_email_detector"

        def detect(self, text: str) -> DetectorResult:
            if "@" in text and "." in text:
                return DetectorResult(
                    triggered=True,
                    detector_type="personal_data",
                    matched_count=1,
                    matched_patterns=["email_pattern"],
                )
            return DetectorResult(
                triggered=False,
                detector_type="personal_data",
                matched_count=0,
                matched_patterns=[],
            )

    register_detector(CustomDetector())

    from app.detectors.registry import get_detectors

    detectors = get_detectors()
    assert len(detectors) == 1
    assert detectors[0].name == "custom_email_detector"

    result = detectors[0].detect("contact me at user@example.com")
    assert result.triggered is True
    assert result.detector_type == "personal_data"
    assert "email_pattern" in result.matched_patterns


@pytest.mark.asyncio
async def test_detector_scans_final_messages_with_rag_context(
    app_fixture: FastAPI,
) -> None:
    """Сканируется messages list (RAG-фрагменты), не только body.content.

    Сценарий: RAG-фрагмент содержит секрет. Детектор должен сработать
    на содержимом фрагмента, даже если сам user query чистый.
    """
    register_detector(_FakeDetector())

    from app.detectors.service import run_detectors

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id
    async with factory() as session:
        role = Role(workspace_id=workspace_id, name="admin", policy={}, is_builtin=True)
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email="rag@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()

        settings = Settings(detectors_enabled=True)
        # messages: system prompt + RAG fragment with SECRET + clean user query
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "system", "content": "Context: The API KEY is SECRET sk-xyz789"},
            {"role": "user", "content": "What is the answer?"},
        ]
        await run_detectors(
            session,
            settings,
            user,
            "model-rag",
            "conv-rag",
            messages,
            "external",
        )
        await session.commit()

    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(AuditLog.action == "security.detector_triggered")
        )
        audit = result.scalar_one_or_none()
        assert audit is not None  # Детектор сработал на RAG-фрагмент


@pytest.mark.asyncio
async def test_detector_runs_on_fallback_to_external(
    app_fixture: FastAPI,
) -> None:
    """primary=local → fallback=external → детектор запускается для fallback.

    Проверяет пункт 1 дизайна: детекция в точке фактического вызова,
    не в prepare_chat. При primary=local детекторы пропускаются,
    но при fallback на external — запускаются.
    """
    register_detector(_FakeDetector())

    from app.detectors.service import run_detectors

    factory = app_fixture.state.db_session_factory
    workspace_id = app_fixture.state.workspace_id

    # Симулируем: primary=local (detectors skip), затем fallback=external
    async with factory() as session:
        role = Role(workspace_id=workspace_id, name="admin", policy={}, is_builtin=True)
        session.add(role)
        await session.flush()
        user = User(
            workspace_id=workspace_id,
            email="fallback@orqion.local",
            password_hash="hash",
            role_id=role.id,
            is_active=True,
            auth_method="local",
        )
        session.add(user)
        await session.flush()

        settings = Settings(detectors_enabled=True)

        # Attempt 1: local provider — detectors skip
        await run_detectors(
            session,
            settings,
            user,
            "model-local",
            "conv-fallback",
            [{"role": "user", "content": "SECRET data"}],
            "local",
        )

        # Attempt 2: fallback to external — detectors run
        await run_detectors(
            session,
            settings,
            user,
            "model-external",
            "conv-fallback",
            [{"role": "user", "content": "SECRET data"}],
            "external",
        )
        await session.commit()

    # Audit log: только одна запись (для external fallback), не для local
    async with factory() as session:
        result = await session.execute(
            select(AuditLog).where(
                AuditLog.action == "security.detector_triggered",
                AuditLog.workspace_id == workspace_id,
            )
        )
        audits = result.scalars().all()
        assert len(audits) == 1
        assert audits[0].meta["model_id"] == "model-external"
