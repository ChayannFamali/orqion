"""Эмбеддинги чанков (T-211, S-24).

Интерфейс EmbeddingBackend + две реализации:
- LocalEmbeddingBackend: FlagEmbedding/bge-m3, lazy import, CPU fallback (extras [full])
- ProviderEmbeddingBackend: httpx POST /v1/embeddings к OpenAI-совместимому провайдеру

Пакетная обработка с прогрессом, нормализация L2.
Модель фиксируется в index_version.embedding_model (ADR-8).
Смена модели → новая index_version, векторы не смешиваются.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

# ---------------------------------------------------------------------------
# Контракты
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Источников эмбеддингов: локальная модель или провайдер."""

    def model_name(self) -> str:
        """Имя модели — записывается в index_version.embedding_model."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Эмбеддинги для списка текстов. Возвращает ненормализованные векторы."""
        ...


@dataclass(frozen=True)
class EmbeddedChunk:
    """Чанк с эмбеддингом — результат embed_batch.

    chunk_id — UUID из таблицы chunk (String(36)). Заполняется на стороне
    вызывающего (T-214 pipeline), не в embed_batch — эмбеддер не знает про БД.
    VectorStore.upsert использует chunk_id как ключ маппинга.
    """

    text: str
    vector: list[float]
    ordinal: int
    model: str
    chunk_id: str = ""


# ---------------------------------------------------------------------------
# Нормализация L2
# ---------------------------------------------------------------------------


def normalize_l2(vec: list[float]) -> list[float]:
    """Нормализация вектора по L2-норме.

    Единообразно применяется ко всем векторам независимо от источника.
    Нулевой вектор возвращается как есть (не делится на 0).
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


# ---------------------------------------------------------------------------
# Локальная реализация — FlagEmbedding/bge-m3
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "BAAI/bge-m3"


class LocalEmbeddingBackend:
    """Локальные эмбеддинги через FlagEmbedding (bge-m3).

    Требует extras [full] (тянет torch). Lazy import — модуль не загружается
    если не установлен. Device auto-detection: GPU если доступен, иначе CPU.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str | None = None) -> None:
        self._model_name = model_name
        self._device = device
        self._model: object | None = None

    def model_name(self) -> str:
        return self._model_name

    def _get_model(self) -> object:
        if self._model is not None:
            return self._model

        try:
            from FlagEmbedding import BGEM3FlagModel  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "FlagEmbedding не установлен. Установите orqion[full]: pip install orqion[full]"
            ) from e

        if self._device is None:
            self._device = self._detect_device()

        self._model = BGEM3FlagModel(
            self._model_name,
            use_fp16=self._device.startswith("cuda"),
            device=self._device,
        )
        return self._model

    @staticmethod
    def _detect_device() -> str:
        """Авто-определение устройства: GPU если доступен, иначе CPU."""
        try:
            import torch  # type: ignore[import-not-found]

            if torch.cuda.is_available():
                return "cuda"
        except ImportError:
            pass
        return "cpu"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Синхронный вызов FlagEmbedding — в async-контексте через to_thread."""
        import asyncio

        model = self._get_model()
        # BGEM3FlagModel.encode возвращает dict с ключом 'dense_vecs'
        result = await asyncio.to_thread(_encode_bge_m3, model, list(texts))
        return result


def _encode_bge_m3(model: Any, texts: list[str]) -> list[list[float]]:
    """Вызывает BGEM3FlagModel.encode и извлекает dense векторы.

    Вынесено в функцию для to_thread — model.encode синхронный.
    """
    output = model.encode(texts, return_dense=True)
    # BGEM3FlagModel.encode возвращает dict: {'dense_vecs': [[...], ...], ...}
    dense: list[list[float]] = []
    vecs = output["dense_vecs"]
    for vec in vecs:
        dense.append([float(x) for x in vec])
    return dense


# ---------------------------------------------------------------------------
# Реализация через провайдера — /v1/embeddings
# ---------------------------------------------------------------------------


class ProviderEmbeddingBackend:
    """Эмбеддинги через OpenAI-совместимый /v1/embeddings endpoint.

    Основной путь для minimal/standard без GPU. Провайдер уже
    запущен для чата (Ollama, LM Studio, внешний API).
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    def model_name(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """POST /v1/embeddings с batch текстов."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload = {
            "model": self._model,
            "input": list(texts),
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/embeddings",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        # OpenAI format: {"data": [{"embedding": [...]}, ...]}
        embeddings: list[list[float]] = []
        for item in data["data"]:
            embeddings.append([float(x) for x in item["embedding"]])
        return embeddings


# ---------------------------------------------------------------------------
# Пакетная обработка с прогрессом
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int], None]
"""callback(done, total) — вызывается после каждой пачки."""


async def embed_batch(
    texts: Sequence[str],
    backend: EmbeddingBackend,
    batch_size: int = 32,
    on_progress: ProgressCallback | None = None,
) -> list[EmbeddedChunk]:
    """Пакетная обработка чанков с эмбеддингом и нормализацией.

    Args:
        texts: список текстов для эмбеддинга.
        backend: источник эмбеддингов (локальный или провайдер).
        batch_size: размер пачки.
        on_progress: callback(done, total) после каждой пачки.

    Returns:
        Список EmbeddedChunk с нормализованными векторами.
    """
    total = len(texts)
    if total == 0:
        return []

    model = backend.model_name()
    results: list[EmbeddedChunk] = []
    done = 0

    for start in range(0, total, batch_size):
        batch = texts[start : start + batch_size]
        vectors = await backend.embed(batch)

        for i, vec in enumerate(vectors):
            normalized = normalize_l2(vec)
            results.append(
                EmbeddedChunk(
                    text=batch[i],
                    vector=normalized,
                    ordinal=start + i,
                    model=model,
                )
            )

        done += len(batch)
        if on_progress is not None:
            on_progress(done, total)

    return results
