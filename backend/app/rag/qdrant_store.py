"""Векторное хранилище: Qdrant (T-213, S-25).

Вторая реализация VectorStore Protocol. Опциональная — профиль full.
Lazy import qdrant_client — профиль minimal не требует установки.

chunk_id (UUID String(36)) используется как Point ID напрямую — без хеширования,
без маппинга. Qdrant нативно поддерживает UUID как ID точек.

Dense: named vector "dense", size=1024, distance=COSINE.
Sparse: named vector "text_sparse", простой TF-токенизатор (не BM25).

Различие качества поиска между бэкендами:
- SQLite: FTS5 с BM25 (настоящий BM25 с IDF и длиной документа)
- Qdrant: простой TF без IDF — приближение, не полноценный BM25
Это сознательное упрощение: Qdrant не на критическом пути minimal-профиля.
Расхождение зафиксировано в planning.md (T-213).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from app.rag.embeddings import EmbeddedChunk
from app.rag.vector_store import EMBEDDING_DIM, Hit

_TOKEN_RE = re.compile(r"\w+")


def _hash_token(token: str) -> int:
    """Детерминированный хеш токена в uint32."""
    return int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")


def _tokenize(text: str) -> tuple[list[int], list[float]]:
    """Простой TF-токенизатор для sparse-поиска в Qdrant.

    lowercase → токены (\\w+) → SHA-256 хеш → uint32 индекс → TF значение.
    Не BM25: нет IDF, нет нормализации по длине документа.
    Возвращает (indices, values) для SparseVector.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    if not tokens:
        return [], []

    counts: dict[int, float] = {}
    for token in tokens:
        h = _hash_token(token)
        counts[h] = counts.get(h, 0.0) + 1.0

    indices = sorted(counts.keys())
    values = [counts[i] for i in indices]
    return indices, values


class QdrantVectorStore:
    """Векторное хранилище на Qdrant.

    Опциональная реализация VectorStore для профиля full.
    Lazy import qdrant_client — профиль minimal не требует установки.

    chunk_id (UUID) используется как Point ID напрямую.
    Dense: named vector "dense", size=1024, distance=COSINE.
    Sparse: named vector "text_sparse", простой TF-токенизатор.
    """

    def __init__(
        self,
        url: str,
        api_key: str | None = None,
        collection_name: str = "orqion_chunks",
    ) -> None:
        try:
            from qdrant_client import AsyncQdrantClient
        except ImportError as e:
            raise ImportError(
                "qdrant-client не установлен. "
                "Установите orqion[vector-qdrant]: pip install orqion[vector-qdrant]"
            ) from e

        self._client = AsyncQdrantClient(url=url, api_key=api_key)
        self._collection_name = collection_name
        self._collection_ready = False

    async def _ensure_collection(self) -> None:
        """Создание коллекции при первом обращении."""
        if self._collection_ready:
            return

        from qdrant_client.models import (
            Distance,
            SparseVectorParams,
            VectorParams,
        )

        collections = await self._client.get_collections()
        existing = {c.name for c in collections.collections}

        if self._collection_name not in existing:
            await self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config={
                    "dense": VectorParams(
                        size=EMBEDDING_DIM,
                        distance=Distance.COSINE,
                    ),
                },
                sparse_vectors_config={
                    "text_sparse": SparseVectorParams(),
                },
            )

        self._collection_ready = True

    async def upsert(self, index_version_id: str, chunks: Sequence[EmbeddedChunk]) -> None:
        """Запись чанков: dense + sparse векторы, payload с text и index_version_id."""
        from qdrant_client.models import PointStruct, SparseVector

        await self._ensure_collection()

        points: list[PointStruct] = []
        for chunk in chunks:
            if not chunk.chunk_id:
                raise ValueError("EmbeddedChunk.chunk_id должен быть заполнен (UUID)")

            sparse_indices, sparse_values = _tokenize(chunk.text)

            vector: dict[str, object] = {"dense": chunk.vector}
            if sparse_indices:
                vector["text_sparse"] = SparseVector(
                    indices=sparse_indices,
                    values=sparse_values,
                )

            points.append(
                PointStruct(
                    id=chunk.chunk_id,
                    vector=vector,
                    payload={
                        "text": chunk.text,
                        "index_version_id": index_version_id,
                    },
                )
            )

        await self._client.upsert(
            collection_name=self._collection_name,
            points=points,
        )

    async def search_dense(self, index_version_id: str, vec: list[float], k: int = 10) -> list[Hit]:
        """Плотный поиск: cosine similarity через Qdrant."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        await self._ensure_collection()

        results = await self._client.query_points(
            collection_name=self._collection_name,
            query=vec,
            using="dense",
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="index_version_id",
                        match=MatchValue(value=index_version_id),
                    )
                ]
            ),
            limit=k,
            with_payload=True,
        )

        return [
            Hit(
                chunk_id=str(point.id),
                score=point.score,
                text=point.payload.get("text", "") if point.payload else "",
            )
            for point in results.points
        ]

    async def search_sparse(self, index_version_id: str, query: str, k: int = 10) -> list[Hit]:
        """Разреженный поиск: TF-токенизация запроса → Qdrant sparse vectors."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue, SparseVector

        await self._ensure_collection()

        sparse_indices, sparse_values = _tokenize(query)
        if not sparse_indices:
            return []

        results = await self._client.query_points(
            collection_name=self._collection_name,
            query=SparseVector(indices=sparse_indices, values=sparse_values),
            using="text_sparse",
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="index_version_id",
                        match=MatchValue(value=index_version_id),
                    )
                ]
            ),
            limit=k,
            with_payload=True,
        )

        return [
            Hit(
                chunk_id=str(point.id),
                score=point.score,
                text=point.payload.get("text", "") if point.payload else "",
            )
            for point in results.points
        ]

    async def drop_version(self, index_version_id: str) -> None:
        """Удаление всех точек версии индекса по фильтру."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        await self._ensure_collection()

        await self._client.delete(
            collection_name=self._collection_name,
            points_filter=Filter(
                must=[
                    FieldCondition(
                        key="index_version_id",
                        match=MatchValue(value=index_version_id),
                    )
                ]
            ),
        )

    async def fetch_dense_all(self, index_version_id: str) -> list[tuple[str, list[float]]]:
        """Выгрузка всех плотных векторов версии: пары (chunk_id, вектор).

        Штатный scroll по фильтру версии индекса, постранично.
        Точки без плотного вектора (только разреженный) пропускаются.
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        await self._ensure_collection()

        results: list[tuple[str, list[float]]] = []
        offset: int | str | None = None
        while True:
            points, next_offset = await self._client.scroll(
                collection_name=self._collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="index_version_id",
                            match=MatchValue(value=index_version_id),
                        )
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=["dense"],
            )
            for point in points:
                vector_data = point.vector
                if not isinstance(vector_data, dict):
                    continue
                dense = vector_data.get("dense")
                if not isinstance(dense, list):
                    continue
                results.append((str(point.id), [float(v) for v in dense]))
            if next_offset is None:
                break
            offset = next_offset
        return results

    async def close(self) -> None:
        """Закрытие клиента."""
        await self._client.close()
