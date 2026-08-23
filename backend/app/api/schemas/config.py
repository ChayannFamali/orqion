"""Схемы API для config export/import (T-425)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConfigExportResponse(BaseModel):
    """Ответ: YAML-конфигурация."""

    yaml: str


class ConfigImportRequest(BaseModel):
    """Запрос: импорт YAML-конфигурации."""

    yaml: str
    dry_run: bool = False


class ImportResultResponse(BaseModel):
    """Результат импорта."""

    roles_created: int = 0
    roles_updated: int = 0
    roles_unchanged: int = 0
    routing_rules_replaced: bool = False
    routing_rules_count: int = 0
    corpora_created: int = 0
    corpora_updated: int = 0
    corpora_unchanged: int = 0
    warnings: list[str] = Field(default_factory=list)
