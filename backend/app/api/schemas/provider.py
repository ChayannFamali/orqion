"""Схемы запроса и ответа для provider/model эндпоинтов."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Канонический набор видов провайдеров (T-437). Свободный текст здесь —
# источник расхождений между потребителями (прецедент BUG-011), поэтому
# валидация на уровне API-схемы, а не только в UI.
ProviderKind = Literal["ollama", "lmstudio", "external"]

# Единый статус скачивания модели (T-437, часть А) — общий для Ollama и
# LM Studio, независимо от нативных форматов каждого провайдера.
DownloadStatus = Literal["pending", "downloading", "completed", "error", "already_downloaded"]


class ProviderCreate(BaseModel):
    kind: ProviderKind
    base_url: str
    api_key: str | None = None
    enabled: bool = True


class ProviderUpdate(BaseModel):
    kind: ProviderKind | None = None
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None


class DownloadModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=512)


class DownloadStatusResponse(BaseModel):
    """Статус скачивания модели — ответ старта и поллинга."""

    job_id: str | None = None
    status: DownloadStatus
    percent: float | None = None
    error: str | None = None
    message: str | None = None


class ModelResponse(BaseModel):
    id: str
    alias: str
    upstream_name: str
    locality: str
    provider_kind: str | None = None
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


class ModelCreate(BaseModel):
    alias: str
    upstream_name: str
    locality: str = "local"
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    supports_reasoning: bool = False
    cost_in: float | None = None
    cost_out: float | None = None
    enabled: bool = True


class ModelUpdate(BaseModel):
    alias: str | None = None
    upstream_name: str | None = None
    locality: str | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    supports_reasoning: bool | None = None
    cost_in: float | None = None
    cost_out: float | None = None
    enabled: bool | None = None


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]
