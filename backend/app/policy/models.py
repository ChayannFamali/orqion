"""Модель Policy: pydantic, валидация сентинелов.

Числовые поля: null = без ограничения.
Списковые поля: ["*"] = все.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

WILDCARD = "*"


class Policy(BaseModel):
    """Декларативная политика роли. Соответствует arch.md §5.2."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    models: list[str] = Field(default_factory=list)
    max_input_tokens: int | None = Field(default=None)
    max_output_tokens: int | None = Field(default=None)
    reasoning: str = Field(default="off")
    budget: dict[str, int] | None = Field(default=None)
    rpm: int | None = Field(default=None)
    tpm: int | None = Field(default=None)
    corpora: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)

    @field_validator("models", "corpora", "capabilities")
    @classmethod
    def validate_list_fields(cls, v: list[str]) -> list[str]:
        if not v:
            return v
        if WILDCARD in v and len(v) > 1:
            raise ValueError("'*' cannot be combined with other values")
        return v

    @field_validator("reasoning")
    @classmethod
    def validate_reasoning(cls, v: str) -> str:
        if v not in ("off", "optional", "on"):
            raise ValueError("reasoning must be 'off', 'optional', or 'on'")
        return v

    @field_validator("max_input_tokens", "max_output_tokens", "rpm", "tpm")
    @classmethod
    def validate_non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("numeric policy fields cannot be negative")
        return v

    def is_unlimited(self, field_name: str) -> bool:
        """True, если поле не ограничено (None для чисел, ['*'] для списков)."""
        value = getattr(self, field_name)
        if value is None:
            return True
        if isinstance(value, list):
            return WILDCARD in value
        return False
