"""Схемы запроса и ответа для provider/model эндпоинтов."""

from __future__ import annotations

from pydantic import BaseModel


class ProviderCreate(BaseModel):
    kind: str
    base_url: str
    api_key: str | None = None
    enabled: bool = True


class ProviderUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None


class ModelResponse(BaseModel):
    id: str
    alias: str
    upstream_name: str
    locality: str
    max_input_tokens: int | None
    max_output_tokens: int | None
    supports_reasoning: bool
    cost_in: float | None
    cost_out: float | None
    enabled: bool


class ProviderResponse(BaseModel):
    id: str
    kind: str
    base_url: str
    enabled: bool
    capabilities: dict[str, object]
    models: list[ModelResponse] = []


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]
