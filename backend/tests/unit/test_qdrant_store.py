"""Тесты QdrantVectorStore (T-213, S-25).

Проверки:
- ImportError при отсутствии qdrant-client (профиль minimal)
- Protocol conformance: методы присутствуют
- Интеграционные тесты требуют запущенного Qdrant — skip без qdrant-client
"""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from app.rag.qdrant_store import QdrantVectorStore

# ---------------------------------------------------------------------------
# ImportError при отсутствии qdrant-client
# ---------------------------------------------------------------------------


def _qdrant_installed() -> bool:
    """Проверяет, установлен ли qdrant-client."""
    return importlib.util.find_spec("qdrant_client") is not None


def test_import_error_without_qdrant() -> None:
    """QdrantVectorStore.__init__ → ImportError без qdrant-client."""
    if _qdrant_installed():
        pytest.skip("qdrant-client установлен — ImportError не применим")

    from app.rag.qdrant_store import QdrantVectorStore

    with pytest.raises(ImportError, match="qdrant-client не установлен"):
        QdrantVectorStore(url="http://localhost:6333")


# ---------------------------------------------------------------------------
# Protocol conformance — без экземпляра (не требует qdrant-client)
# ---------------------------------------------------------------------------


def test_qdrant_vector_store_has_protocol_methods() -> None:
    """QdrantVectorStore имеет все методы VectorStore Protocol."""
    from app.rag.qdrant_store import QdrantVectorStore

    assert hasattr(QdrantVectorStore, "upsert")
    assert hasattr(QdrantVectorStore, "search_dense")
    assert hasattr(QdrantVectorStore, "search_sparse")
    assert hasattr(QdrantVectorStore, "drop_version")


@pytest.mark.skipif(
    not _qdrant_installed(),
    reason="qdrant-client не установлен",
)
async def test_qdrant_vector_store_satisfies_protocol() -> None:
    """QdrantVectorStore удовлетворяет VectorStore Protocol (isinstance)."""
    from app.rag.qdrant_store import QdrantVectorStore
    from app.rag.vector_store import VectorStore

    store = QdrantVectorStore(url="http://localhost:6333")
    assert isinstance(store, VectorStore)
    await store.close()


# ---------------------------------------------------------------------------
# Интеграционные тесты — требуют запущенного Qdrant
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _qdrant_installed(),
    reason="qdrant-client не установлен",
)
class TestQdrantIntegration:
    """Интеграционные тесты с реальным Qdrant.

    Запуск: docker run -p 6333:6333 qdrant/qdrant
    Затем: ORQION_VECTOR_STORE=qdrant ORQION_QDRANT_URL=http://localhost:6333
    """

    @pytest.fixture
    async def store(self) -> AsyncIterator[QdrantVectorStore]:
        from app.rag.qdrant_store import QdrantVectorStore

        s = QdrantVectorStore(url="http://localhost:6333")
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_upsert_and_search_dense(self, store: QdrantVectorStore) -> None:
        """upsert + search_dense: запись и поиск по dense вектору."""
        from app.rag.embeddings import EmbeddedChunk
        from app.rag.vector_store import EMBEDDING_DIM

        vec = [0.0] * EMBEDDING_DIM
        vec[0] = 1.0
        chunk = EmbeddedChunk(
            text="hello world",
            vector=vec,
            ordinal=0,
            model="test",
            chunk_id="test-0001-uuid",
        )

        await store.upsert("ver-001", [chunk])

        results = await store.search_dense("ver-001", vec, k=1)
        assert len(results) >= 1
        assert results[0].text == "hello world"
        assert results[0].chunk_id == "test-0001-uuid"

    @pytest.mark.asyncio
    async def test_search_sparse(self, store: QdrantVectorStore) -> None:
        """search_sparse: поиск по sparse вектору."""
        from app.rag.embeddings import EmbeddedChunk
        from app.rag.vector_store import EMBEDDING_DIM

        vec = [0.0] * EMBEDDING_DIM
        vec[0] = 1.0
        chunk = EmbeddedChunk(
            text="hello world foo",
            vector=vec,
            ordinal=0,
            model="test",
            chunk_id="test-0002-uuid",
        )

        await store.upsert("ver-001", [chunk])

        results = await store.search_sparse("ver-001", "hello", k=10)
        assert len(results) >= 1
        assert results[0].chunk_id == "test-0002-uuid"

    @pytest.mark.asyncio
    async def test_drop_version(self, store: QdrantVectorStore) -> None:
        """drop_version удаляет все точки версии."""
        from app.rag.embeddings import EmbeddedChunk
        from app.rag.vector_store import EMBEDDING_DIM

        vec = [0.0] * EMBEDDING_DIM
        vec[0] = 1.0
        chunk = EmbeddedChunk(
            text="to delete",
            vector=vec,
            ordinal=0,
            model="test",
            chunk_id="test-0003-uuid",
        )

        await store.upsert("ver-drop", [chunk])
        await store.drop_version("ver-drop")

        results = await store.search_dense("ver-drop", vec, k=10)
        assert results == []
