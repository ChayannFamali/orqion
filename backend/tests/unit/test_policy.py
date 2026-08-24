"""Тесты Policy: валидация, пресеты, resolve, enforce (все ветви + admin)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from app.db.models import Role, User
from app.db.workspace import ensure_default_workspace
from app.errors import (
    ConfigurationError,
    ContextLimitExceeded,
    DataClassViolation,
    ModelNotAllowed,
    RateLimitExceeded,
)
from app.policy.enforce import enforce
from app.policy.models import Budget, Policy
from app.policy.presets import BUILTIN_ROLES
from app.policy.rate_limiter import RateLimiter
from app.policy.resolve import resolve_policy
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class FakeAction:
    model_alias: str
    model_locality: str
    input_tokens: int
    output_tokens: int = 0
    corpus_data_class: str | None = None
    corpus_name: str | None = None
    corpus_names: list[str] | None = None


class TestPolicyValidation:
    def test_wildcard_alone_in_list(self) -> None:
        p = Policy(models=["*"])
        assert p.models == ["*"]

    def test_wildcard_cannot_combine_with_others(self) -> None:
        with pytest.raises(ValueError, match="cannot be combined"):
            Policy(models=["*", "external/*"])

    def test_reasoning_valid_values(self) -> None:
        for val in ("off", "optional", "on"):
            Policy(reasoning=val)

    def test_reasoning_invalid_value(self) -> None:
        with pytest.raises(ValueError):
            Policy(reasoning="always")

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValueError):
            Policy(max_input_tokens=-1)

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValueError):
            Policy(data_classes=["public"])  # type: ignore[call-arg]

    def test_is_unlimited_numeric(self) -> None:
        p = Policy(max_input_tokens=None)
        assert p.is_unlimited("max_input_tokens") is True
        p2 = Policy(max_input_tokens=16000)
        assert p2.is_unlimited("max_input_tokens") is False

    def test_is_unlimited_list(self) -> None:
        p = Policy(models=["*"])
        assert p.is_unlimited("models") is True
        p2 = Policy(models=["local/*"])
        assert p2.is_unlimited("models") is False


class TestPresets:
    def test_admin_is_unlimited_everywhere(self) -> None:
        admin = BUILTIN_ROLES["admin"]
        assert admin.is_unlimited("models")
        assert admin.is_unlimited("max_input_tokens")
        assert admin.is_unlimited("max_output_tokens")
        assert admin.is_unlimited("budget")
        assert admin.is_unlimited("rpm")
        assert admin.is_unlimited("tpm")
        assert admin.is_unlimited("corpora")
        assert admin.is_unlimited("capabilities")

    def test_support_has_limits(self) -> None:
        support = BUILTIN_ROLES["support"]
        assert support.max_input_tokens == 16000
        assert support.reasoning == "off"
        assert support.models == ["local/*"]
        assert support.capabilities == ["chat"]

    def test_architect_has_long_context(self) -> None:
        arch = BUILTIN_ROLES["architect"]
        assert arch.max_input_tokens == 200000
        assert "manage_corpora" in arch.capabilities

    def test_manager_has_view_analytics_not_manage_corpora(self) -> None:
        mgr = BUILTIN_ROLES["manager"]
        assert "view_analytics" in mgr.capabilities
        assert "manage_corpora" not in mgr.capabilities

    def test_all_five_presets_present(self) -> None:
        assert set(BUILTIN_ROLES.keys()) == {
            "support",
            "developer",
            "architect",
            "manager",
            "admin",
        }


class TestEnforceAdmin:
    """admin проходит все ветви enforce — ни одного отказа."""

    def test_admin_passes_all_checks(self) -> None:
        admin = BUILTIN_ROLES["admin"]
        action = FakeAction(
            model_alias="external/claude-sonnet",
            model_locality="external",
            input_tokens=500000,
            output_tokens=100000,
        )
        enforce(admin, action)

    def test_admin_passes_data_class_check(self) -> None:
        """admin не может обойти ADR-12: К2/К3 → только local."""
        admin = BUILTIN_ROLES["admin"]
        action = FakeAction(
            model_alias="external/gpt-4",
            model_locality="external",
            input_tokens=100,
            output_tokens=100,
            corpus_data_class="К2",
        )
        with pytest.raises(DataClassViolation):
            enforce(admin, action)

    def test_admin_passes_with_local_model_on_k2(self) -> None:
        admin = BUILTIN_ROLES["admin"]
        action = FakeAction(
            model_alias="local/qwen3-14b",
            model_locality="local",
            input_tokens=500000,
            output_tokens=100000,
            corpus_data_class="К2",
        )
        enforce(admin, action)


class TestEnforceRejects:
    def test_data_class_violation_external_model(self) -> None:
        support = BUILTIN_ROLES["support"]
        action = FakeAction(
            model_alias="external/gpt-4",
            model_locality="external",
            input_tokens=100,
            corpus_data_class="К3",
        )
        with pytest.raises(DataClassViolation) as exc_info:
            enforce(support, action)
        assert exc_info.value.constraint is not None
        assert exc_info.value.constraint["data_class"] == "К3"

    def test_model_not_allowed(self) -> None:
        support = BUILTIN_ROLES["support"]
        action = FakeAction(
            model_alias="external/claude-sonnet",
            model_locality="external",
            input_tokens=100,
        )
        with pytest.raises(ModelNotAllowed) as exc_info:
            enforce(support, action)
        assert exc_info.value.constraint is not None
        assert "external/claude-sonnet" in exc_info.value.constraint["model"]

    def test_context_limit_exceeded(self) -> None:
        support = BUILTIN_ROLES["support"]
        action = FakeAction(
            model_alias="local/qwen3-8b",
            model_locality="local",
            input_tokens=20000,
        )
        with pytest.raises(ContextLimitExceeded) as exc_info:
            enforce(support, action)
        assert exc_info.value.constraint is not None
        assert exc_info.value.constraint["limit"] == 16000

    def test_context_limit_passes_at_boundary(self) -> None:
        support = BUILTIN_ROLES["support"]
        action = FakeAction(
            model_alias="local/qwen3-8b",
            model_locality="local",
            input_tokens=16000,
        )
        enforce(support, action)

    def test_tpm_exceeded(self) -> None:
        """TPM token bucket: сумма токенов в окне > tpm → 429."""
        policy = Policy(
            models=["local/*"],
            max_input_tokens=100000,
            tpm=20000,
        )
        rl = RateLimiter()
        action = FakeAction(
            model_alias="local/qwen3-8b",
            model_locality="local",
            input_tokens=15000,
        )
        enforce(policy, action, rate_limiter=rl, user_id="user-1")

        action2 = FakeAction(
            model_alias="local/qwen3-8b",
            model_locality="local",
            input_tokens=10000,
        )
        with pytest.raises(RateLimitExceeded) as exc_info:
            enforce(policy, action2, rate_limiter=rl, user_id="user-1")
        assert exc_info.value.constraint is not None
        assert exc_info.value.constraint["type"] == "tpm"
        assert "reset_in_seconds" in exc_info.value.constraint

    def test_rpm_exceeded(self) -> None:
        """RPM token bucket: количество запросов в окне > rpm → 429."""
        policy = Policy(
            models=["local/*"],
            max_input_tokens=100000,
            rpm=2,
        )
        rl = RateLimiter()
        action = FakeAction(
            model_alias="local/qwen3-8b",
            model_locality="local",
            input_tokens=100,
        )
        enforce(policy, action, rate_limiter=rl, user_id="user-1")
        enforce(policy, action, rate_limiter=rl, user_id="user-1")

        with pytest.raises(RateLimitExceeded) as exc_info:
            enforce(policy, action, rate_limiter=rl, user_id="user-1")
        assert exc_info.value.constraint is not None
        assert exc_info.value.constraint["type"] == "rpm"
        assert "reset_in_seconds" in exc_info.value.constraint

    def test_rate_limiter_isolated_per_user(self) -> None:
        """Лимиты независимы для разных пользователей."""
        policy = Policy(models=["local/*"], max_input_tokens=100000, rpm=1)
        rl = RateLimiter()
        action = FakeAction(
            model_alias="local/qwen3-8b",
            model_locality="local",
            input_tokens=100,
        )
        enforce(policy, action, rate_limiter=rl, user_id="user-1")
        enforce(policy, action, rate_limiter=rl, user_id="user-2")

    def test_rate_limiter_none_skips_check(self) -> None:
        """Без rate_limiter проверки rpm/tpm пропускаются."""
        policy = Policy(models=["*"], max_input_tokens=100000, rpm=1, tpm=1)
        action = FakeAction(
            model_alias="local/qwen3-8b",
            model_locality="local",
            input_tokens=100,
        )
        enforce(policy, action)
        enforce(policy, action)

    def test_budget_check_is_noop_until_t117(self) -> None:
        """Проверка бюджета блокирована до T-117 (нет таблицы usage_event).

        Сравнение одного запроса с месячным лимитом бессмысленно —
        один запрос никогда не превысит месячный бюджет.
        """
        policy = Policy(
            models=["local/*"],
            max_input_tokens=10_000_000,
            budget=Budget(tokens_month=5000000, cost_month=10),
        )
        action = FakeAction(
            model_alias="local/qwen3-8b",
            model_locality="local",
            input_tokens=6_000_000,
        )
        enforce(policy, action)

    def test_rate_limiter_without_user_id_raises(self) -> None:
        """rate_limiter без user_id — ConfigurationError, не тихий пропуск."""
        policy = Policy(models=["local/*"], max_input_tokens=100000, rpm=10)
        rl = RateLimiter()
        action = FakeAction(
            model_alias="local/qwen3-8b",
            model_locality="local",
            input_tokens=100,
        )
        with pytest.raises(ConfigurationError):
            enforce(policy, action, rate_limiter=rl, user_id=None)


class TestEnforceWildcard:
    def test_wildcard_model_pattern_matches(self) -> None:
        developer = BUILTIN_ROLES["developer"]
        action = FakeAction(
            model_alias="external/openai/gpt-4",
            model_locality="external",
            input_tokens=100,
        )
        enforce(developer, action)

    def test_local_wildcard_matches(self) -> None:
        support = BUILTIN_ROLES["support"]
        action = FakeAction(
            model_alias="local/qwen3-14b",
            model_locality="local",
            input_tokens=100,
        )
        enforce(support, action)


class TestResolvePolicy:
    @pytest.mark.asyncio
    async def test_resolve_returns_policy_from_role(self, db_session: AsyncSession) -> None:
        ws_id = await ensure_default_workspace(db_session)
        await db_session.flush()

        role = Role(
            workspace_id=ws_id,
            name="custom",
            is_builtin=False,
            policy=BUILTIN_ROLES["developer"].model_dump(),
        )
        db_session.add(role)
        await db_session.flush()

        user = User(
            workspace_id=ws_id,
            email="dev@orqion.local",
            password_hash="$argon2id$stub",
            role_id=role.id,
        )
        db_session.add(user)
        await db_session.flush()

        policy = await resolve_policy(db_session, user)
        assert policy.max_input_tokens == 64000
        assert "external/*" in policy.models


class TestEnforceCorpusVisibility:
    """T-439 (Б1): видимость корпусов в мульти-режиме — проверяется каждый
    выбранный корпус; отказ со списком всех непройденных."""

    def _policy_with_corpora(self, corpora: list[str]) -> Policy:
        return Policy(models=["*"], corpora=corpora)

    def test_all_corpora_allowed_passes(self) -> None:
        policy = self._policy_with_corpora(["public", "team"])
        action = FakeAction(
            model_alias="local/m",
            model_locality="local",
            input_tokens=100,
            corpus_names=["public", "team"],
        )
        enforce(policy, action)

    def test_single_disallowed_corpus_listed_in_constraint(self) -> None:
        from app.errors import Forbidden

        policy = self._policy_with_corpora(["public"])
        action = FakeAction(
            model_alias="local/m",
            model_locality="local",
            input_tokens=100,
            corpus_names=["public", "secret"],
        )
        with pytest.raises(Forbidden) as exc_info:
            enforce(policy, action)
        assert exc_info.value.constraint is not None
        assert exc_info.value.constraint["corpora"] == ["secret"]

    def test_all_disallowed_corpora_listed(self) -> None:
        from app.errors import Forbidden

        policy = self._policy_with_corpora(["public"])
        action = FakeAction(
            model_alias="local/m",
            model_locality="local",
            input_tokens=100,
            corpus_names=["secret-a", "secret-b"],
        )
        with pytest.raises(Forbidden) as exc_info:
            enforce(policy, action)
        assert exc_info.value.constraint is not None
        assert exc_info.value.constraint["corpora"] == ["secret-a", "secret-b"]

    def test_wildcard_corpora_allows_any(self) -> None:
        policy = self._policy_with_corpora(["*"])
        action = FakeAction(
            model_alias="local/m",
            model_locality="local",
            input_tokens=100,
            corpus_names=["anything", "else"],
        )
        enforce(policy, action)

    def test_single_corpus_name_still_enforced(self) -> None:
        """Регресс одиночного режима: список не задан — проверяется имя."""
        from app.errors import Forbidden

        policy = self._policy_with_corpora(["public"])
        action = FakeAction(
            model_alias="local/m",
            model_locality="local",
            input_tokens=100,
            corpus_name="secret",
        )
        with pytest.raises(Forbidden):
            enforce(policy, action)
