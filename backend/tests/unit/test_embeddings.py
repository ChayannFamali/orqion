"""Тесты эмбеддингов (T-211, S-24).

Проверки:
- normalize_l2: единичная норма, нулевой вектор, идемпотентность
- ProviderEmbeddingBackend: mock httpx, парсинг OpenAI format
- embed_batch: пакетная обработка, нормализация, прогресс callback
- embed_batch: пустой список
- model_name: каждая реализация возвращает своё имя
- Protocol: обе реализации удовлетворяют EmbeddingBackend
"""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from app.rag.embeddings import (
    DEFAULT_MODEL,
    LocalEmbeddingBackend,
    ProviderEmbeddingBackend,
    embed_batch,
    normalize_l2,
)

# ---------------------------------------------------------------------------
# normalize_l2
# ---------------------------------------------------------------------------


def test_normalize_l2_unit_vector() -> None:
    """Нормализованный вектор имеет единичную L2-норму."""
    vec = [3.0, 4.0]
    result = normalize_l2(vec)
    norm = sum(x * x for x in result) ** 0.5
    assert abs(norm - 1.0) < 1e-9


def test_normalize_l2_zero_vector() -> None:
    """Нулевой вектор возвращается как есть (не делится на 0)."""
    vec = [0.0, 0.0, 0.0]
    result = normalize_l2(vec)
    assert result == [0.0, 0.0, 0.0]


def test_normalize_l2_idempotent() -> None:
    """Повторная нормализация не меняет вектор."""
    vec = [1.0, 2.0, 3.0]
    once = normalize_l2(vec)
    twice = normalize_l2(once)
    for a, b in zip(once, twice, strict=True):
        assert abs(a - b) < 1e-9


def test_normalize_l2_preserves_direction() -> None:
    """Нормализация сохраняет направление."""
    vec = [1.0, 0.0]
    result = normalize_l2(vec)
    assert result == [1.0, 0.0]


# ---------------------------------------------------------------------------
# ProviderEmbeddingBackend (mock httpx)
# ---------------------------------------------------------------------------


class FakeBackend:
    """Простой mock EmbeddingBackend для тестов embed_batch."""

    def __init__(self, dim: int = 4, name: str = "fake-model") -> None:
        self._dim = dim
        self._name = name

    def model_name(self) -> str:
        return self._name

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(len(text))] + [0.0] * (self._dim - 1) for text in texts]


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend(dim=4, name="fake-mock")


# ---------------------------------------------------------------------------


def test_provider_backend_model_name() -> None:
    """ProviderEmbeddingBackend возвращает имя модели."""
    backend = ProviderEmbeddingBackend(
        base_url="http://localhost:1234",
        model="text-embedding-3-small",
    )
    assert backend.model_name() == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_provider_backend_embed() -> None:
    """ProviderEmbeddingBackend: mock httpx, парсинг OpenAI format."""
    backend = ProviderEmbeddingBackend(
        base_url="http://localhost:1234",
        model="bge-m3",
        api_key="test-key",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": [
            {"embedding": [1.0, 0.0, 0.0, 0.0]},
            {"embedding": [0.0, 1.0, 0.0, 0.0]},
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        result = await backend.embed(["text one", "text two"])

    assert len(result) == 2
    assert result[0] == [1.0, 0.0, 0.0, 0.0]
    assert result[1] == [0.0, 1.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_provider_backend_embed_no_api_key() -> None:
    """ProviderEmbeddingBackend без api_key — нет Authorization header."""
    backend = ProviderEmbeddingBackend(
        base_url="http://localhost:1234",
        model="bge-m3",
    )

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"embedding": [1.0, 0.0]}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await backend.embed(["text"])

    call_args = mock_post.call_args
    headers = call_args.kwargs.get("headers", {})
    assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# embed_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_batch_basic(fake_backend: FakeBackend) -> None:
    """embed_batch: возвращает EmbeddedChunk для каждого текста."""
    texts = ["hello", "world", "foo"]
    results = await embed_batch(texts, fake_backend, batch_size=2)

    assert len(results) == 3
    for i, chunk in enumerate(results):
        assert chunk.ordinal == i
        assert chunk.text == texts[i]
        assert chunk.model == "fake-mock"
        # Векторы нормализованы
        norm = sum(x * x for x in chunk.vector) ** 0.5
        assert abs(norm - 1.0) < 1e-9 or norm == 0.0


@pytest.mark.asyncio
async def test_embed_batch_empty(fake_backend: FakeBackend) -> None:
    """embed_batch: пустой список — пустой результат."""
    results = await embed_batch([], fake_backend)
    assert results == []


@pytest.mark.asyncio
async def test_embed_batch_progress_callback(fake_backend: FakeBackend) -> None:
    """embed_batch: on_progress вызывается после каждой пачки."""
    texts = ["a", "b", "c", "d", "e"]
    progress_calls: list[tuple[int, int]] = []

    await embed_batch(
        texts, fake_backend, batch_size=2, on_progress=lambda d, t: progress_calls.append((d, t))
    )

    # 3 пачки: [a,b], [c,d], [e]
    assert progress_calls == [(2, 5), (4, 5), (5, 5)]


@pytest.mark.asyncio
async def test_embed_batch_batch_size_1(fake_backend: FakeBackend) -> None:
    """embed_batch: batch_size=1 — каждый текст отдельно."""
    texts = ["x", "y"]
    progress_calls: list[tuple[int, int]] = []

    results = await embed_batch(
        texts, fake_backend, batch_size=1, on_progress=lambda d, t: progress_calls.append((d, t))
    )

    assert len(results) == 2
    assert progress_calls == [(1, 2), (2, 2)]


@pytest.mark.asyncio
async def test_embed_batch_normalization(fake_backend: FakeBackend) -> None:
    """embed_batch: все векторы нормализованы (L2 = 1 или 0)."""
    texts = ["short", "a much longer text that produces a larger embedding value"]
    results = await embed_batch(texts, fake_backend)

    for chunk in results:
        norm = sum(x * x for x in chunk.vector) ** 0.5
        assert abs(norm - 1.0) < 1e-9 or norm == 0.0


@pytest.mark.asyncio
async def test_embed_batch_model_in_each_chunk(fake_backend: FakeBackend) -> None:
    """embed_batch: model в каждом EmbeddedChunk."""
    texts = ["a", "b"]
    results = await embed_batch(texts, fake_backend)

    for chunk in results:
        assert chunk.model == "fake-mock"


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_fake_backend_satisfies_protocol() -> None:
    """FakeBackend удовлетворяет EmbeddingBackend Protocol."""
    from app.rag.embeddings import EmbeddingBackend

    backend = FakeBackend()
    assert isinstance(backend, EmbeddingBackend)


def test_provider_backend_satisfies_protocol() -> None:
    """ProviderEmbeddingBackend удовлетворяет EmbeddingBackend Protocol."""
    from app.rag.embeddings import EmbeddingBackend

    backend = ProviderEmbeddingBackend("http://localhost:1234", "bge-m3")
    assert isinstance(backend, EmbeddingBackend)


# ---------------------------------------------------------------------------
# LocalEmbeddingBackend (без реальной загрузки модели)
# ---------------------------------------------------------------------------


def test_local_backend_model_name() -> None:
    """LocalEmbeddingBackend возвращает имя модели."""
    backend = LocalEmbeddingBackend(model_name="BAAI/bge-m3")
    assert backend.model_name() == "BAAI/bge-m3"


def test_local_backend_default_model() -> None:
    """LocalEmbeddingBackend: default model = DEFAULT_MODEL."""
    backend = LocalEmbeddingBackend()
    assert backend.model_name() == DEFAULT_MODEL


def test_local_backend_import_error_without_flag() -> None:
    """LocalEmbeddingBackend: ImportError если FlagEmbedding не установлен."""
    backend = LocalEmbeddingBackend(device="cpu")

    with (
        patch("builtins.__import__", side_effect=ImportError("no FlagEmbedding")),
        pytest.raises(ImportError, match="FlagEmbedding не установлен"),
    ):
        backend._get_model()
