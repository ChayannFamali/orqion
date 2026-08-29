"""Граф связей документов: семантические кластеры (Т-505).

Документы активной версии индекса представляются средними векторами своих
чанков и группируются сферическим k-means (число групп задаёт
администратор в настройках RAG; без автоподбора и без автоназваний —
решение 4 дизайн-ревью). Результат — узлы-кластеры («Группа N») и
узлы-документы с рёбрами принадлежности.

Объём — только явное усечение (принцип §7.3, образец Т-504): при
превышении ``MAX_DOCUMENTS`` кластеризуется детерминированное подмножество
(сортировка по имени файла), в ответе ``truncated=true`` и полное число
документов — «кластеризация построена по N из M документов».

Кэш — до пересборки версии индекса (решение 3): запись идёт во вложенный
подключ ``stats["clustering"][str(k)]`` строки IndexVersion (не на верхний
уровень словаря — там поля прогресса сборки Т-214); пересборка создаёт
новую строку версии, старый кэш просто не читается. Детерминизм самого
k-means (фиксированный сид) делает повторную сборку идентичной кэшу.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import Chunk, Corpus, Document, IndexVersion
from app.rag.clustering import cluster_vectors
from app.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

MAX_DOCUMENTS = 200

# Вложенный подключ в IndexVersion.stats — отдельно от полей прогресса
# сборки (documents_total, chunks_total, status, error — Т-214).
CACHE_KEY = "clustering"


@dataclass(frozen=True)
class ClusteredDocument:
    """Документ с присвоенной группой."""

    document_id: str
    filename: str
    cluster: int


@dataclass(frozen=True)
class CachedClusters:
    """Валидная запись кэша кластеризации для одного значения k."""

    document_clusters: dict[str, int]
    total_documents: int


@dataclass(frozen=True)
class DocumentGraphBuild:
    """Результат кластеризации: документы по группам + параметры усечения."""

    documents: list[ClusteredDocument]
    total_documents: int
    truncated: bool
    from_cache: bool


async def build_document_graph(
    session: AsyncSession,
    vector_store: VectorStore,
    corpus: Corpus,
    k: int,
) -> DocumentGraphBuild | None:
    """Строит кластеризацию документов активной версии индекса.

    Возвращает None, если активной версии нет (пустой корпус — без
    ошибок, интерфейс показывает пустое состояние).

    При промахе кэша читает все плотные векторы версии (один раз на
    версию индекса) и пишет кэш в ``version.stats``; коммит делает
    вызывающий.
    """
    if corpus.active_index_version_id is None:
        return None
    version = await session.get(IndexVersion, corpus.active_index_version_id)
    if version is None:
        return None

    cached = _read_cache(version, k)
    if cached is not None:
        documents = await _documents_from_cache(session, corpus, cached)
        return DocumentGraphBuild(
            documents=documents,
            total_documents=cached.total_documents,
            truncated=cached.total_documents > len(documents),
            from_cache=True,
        )

    documents, total, truncated = await _cluster_from_vectors(
        session, vector_store, corpus, version.id, k
    )
    _write_cache(version, k, documents, total)
    return DocumentGraphBuild(
        documents=documents,
        total_documents=total,
        truncated=truncated,
        from_cache=False,
    )


async def _cluster_from_vectors(
    session: AsyncSession,
    vector_store: VectorStore,
    corpus: Corpus,
    index_version_id: str,
    k: int,
) -> tuple[list[ClusteredDocument], int, bool]:
    """Полная сборка: выгрузка векторов → средние по документам → k-means."""
    pairs = await vector_store.fetch_dense_all(index_version_id)

    rows = (
        await session.execute(
            select(Chunk.id, Chunk.document_id).where(Chunk.index_version_id == index_version_id)
        )
    ).all()
    chunk_to_document = {str(chunk_id): str(document_id) for chunk_id, document_id in rows}

    vectors_by_document: dict[str, list[list[float]]] = {}
    for chunk_id, vector in pairs:
        document_id = chunk_to_document.get(chunk_id)
        if document_id is None:
            continue
        vectors_by_document.setdefault(document_id, []).append(vector)

    if not vectors_by_document:
        return [], 0, False

    document_rows = (
        await session.execute(
            select(Document.id, Document.filename).where(
                Document.id.in_(list(vectors_by_document.keys())),
                Document.corpus_id == corpus.id,
            )
        )
    ).all()
    filenames = {str(document_id): str(filename) for document_id, filename in document_rows}

    # Детерминированный порядок и усечение — по имени файла (решение
    # дизайн-ревью: детерминизм между вызовами важнее «умного» отбора).
    ordered = sorted(filenames.items(), key=lambda item: (item[1], item[0]))
    total = len(ordered)
    truncated = total > MAX_DOCUMENTS
    selected = ordered[:MAX_DOCUMENTS]

    means: list[list[float]] = []
    for document_id, _filename in selected:
        vectors = vectors_by_document[document_id]
        dim = len(vectors[0])
        means.append([sum(v[i] for v in vectors) / len(vectors) for i in range(dim)])

    labels = await asyncio.to_thread(cluster_vectors, means, k)

    documents = [
        ClusteredDocument(
            document_id=document_id,
            filename=filename,
            cluster=label,
        )
        for (document_id, filename), label in zip(selected, labels, strict=True)
    ]
    return documents, total, truncated


async def _documents_from_cache(
    session: AsyncSession,
    corpus: Corpus,
    cached: CachedClusters,
) -> list[ClusteredDocument]:
    """Документы из кэша, пережившие до текущего состояния корпуса.

    Документы, удалённые после кластеризации, просто не попадают в граф;
    их группа исчезает вместе с ними (пересборка версии пересчитает всё).
    """
    clusters = cached.document_clusters
    if not clusters:
        return []

    rows = (
        await session.execute(
            select(Document.id, Document.filename).where(
                Document.id.in_(list(clusters.keys())),
                Document.corpus_id == corpus.id,
            )
        )
    ).all()
    filenames = {str(document_id): str(filename) for document_id, filename in rows}

    documents = []
    for document_id, label in clusters.items():
        if document_id not in filenames:
            continue
        documents.append(
            ClusteredDocument(
                document_id=document_id,
                filename=filenames[document_id],
                cluster=label,
            )
        )
    documents.sort(key=lambda d: (d.filename, d.document_id))
    return documents


def _read_cache(version: IndexVersion, k: int) -> CachedClusters | None:
    """Валидная запись кэша для данного k или None.

    Защита от частичных/битых записей: если структура подключей не
    совпадает с ожидаемой, запись считается отсутствующей и строится заново.
    """
    stats = version.stats
    if not isinstance(stats, dict):
        return None
    section = stats.get(CACHE_KEY)
    if not isinstance(section, dict):
        return None
    entry = section.get(str(k))
    if not isinstance(entry, dict):
        return None
    raw_clusters = entry.get("document_clusters")
    total = entry.get("total_documents")
    if not isinstance(raw_clusters, dict) or not isinstance(total, int):
        return None
    clusters: dict[str, int] = {}
    for document_id, label in raw_clusters.items():
        if isinstance(label, int):
            clusters[str(document_id)] = label
    return CachedClusters(document_clusters=clusters, total_documents=total)


def _write_cache(
    version: IndexVersion,
    k: int,
    documents: list[ClusteredDocument],
    total: int,
) -> None:
    """Запись кэша во вложенный подключ stats["clustering"][str(k)]."""
    stats = version.stats if isinstance(version.stats, dict) else {}
    section = stats.get(CACHE_KEY)
    if not isinstance(section, dict):
        section = {}
    section[str(k)] = {
        "document_clusters": {d.document_id: d.cluster for d in documents},
        "total_documents": total,
        "built_at": datetime.now(UTC).isoformat(),
    }
    stats[CACHE_KEY] = section
    version.stats = stats
    flag_modified(version, "stats")
