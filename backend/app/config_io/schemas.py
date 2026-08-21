"""Pydantic-схемы для YAML export/import (T-425)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class RoleYAML(BaseModel):
    """Роль в YAML-формате."""

    name: str = Field(min_length=1, max_length=255)
    is_builtin: bool = False
    policy: dict[str, Any]


class RoutingRuleYAML(BaseModel):
    """Правило маршрутизации в YAML-формате."""

    order: int = Field(ge=0)
    is_default: bool = False
    is_terminal: bool = False
    when_corpus_class: list[str] | None = None
    when_role: str | None = None
    when_task: str | None = None
    when_model_alias: str | None = None
    to_models: list[str] | None = None
    allow_locality: list[str] | None = None
    fallback_models: list[str] | None = None
    reason: str = ""


class ConfigYAML(BaseModel):
    """Корневая схема YAML-файла конфигурации."""

    schema_version: int
    roles: list[RoleYAML] = Field(default_factory=list)
    routing_rules: list[RoutingRuleYAML] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_role_names(self) -> ConfigYAML:
        names = [r.name for r in self.roles]
        if len(names) != len(set(names)):
            seen: set[str] = set()
            dupes: set[str] = set()
            for n in names:
                if n in seen:
                    dupes.add(n)
                else:
                    seen.add(n)
            raise ValueError(f"Duplicate role names in YAML: {dupes}")
        return self

    @model_validator(mode="after")
    def validate_unique_rule_orders(self) -> ConfigYAML:
        orders = [r.order for r in self.routing_rules]
        if len(orders) != len(set(orders)):
            seen: set[int] = set()
            dupes: set[int] = set()
            for o in orders:
                if o in seen:
                    dupes.add(o)
                else:
                    seen.add(o)
            raise ValueError(f"Duplicate routing rule orders in YAML: {dupes}")
        return self


class ImportResult(BaseModel):
    """Результат импорта конфигурации."""

    roles_created: int = 0
    roles_updated: int = 0
    roles_unchanged: int = 0
    routing_rules_replaced: bool = False
    routing_rules_count: int = 0
    warnings: list[str] = Field(default_factory=list)
