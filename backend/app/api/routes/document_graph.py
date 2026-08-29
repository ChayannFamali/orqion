"""GET /api/corpora/{corpus_id}/document-graph — граф связей документов (Т-505).

Семантические кластеры документов активной версии индекса: узлы-группы
(«Группа N», без автоназваний) и узлы-документы с рёбрами принадлежности.
Число групп задаёт администратор в настройках RAG (``cluster_count``).

Доступ — отдельная способность ``view_document_graph`` по паттерну
``view_code_graph`` (Т-504): гейт ``WILDCARD or "view_document_graph"``,
без права 404; в посевные пресеты способность не добавляется.

Деградация честная (паттерн Т-444/Т-217): кластеризация требует
опциональную зависимость numpy (экстра ``orqion[graph]``); без неё
эндпоинт отвечает 200 с ``available=false`` и явной причиной, а не
падает и не прячет раздел.

Усечение по числу документов — только явное (принцип §7.3, образец
Т-504): при превышении лимита в ответе ``truncated=true`` и полное
число документов — «кластеризация построена по N из M документов».
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.document_graph import (
    DocumentGraphEdge,
    DocumentGraphNode,
    DocumentGraphResponse,
)
from app.auth.dependencies import current_user
from app.db.models import Corpus, RagSettings, User
from app.db.session import get_session
from app.errors import NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy
from app.rag.clustering import is_clustering_available
from app.rag.document_graph import ClusteredDocument, build_document_graph

router = APIRouter(
    prefix="/api/corpora",
    tags=["corpora"],
    dependencies=[Depends(current_user)],
)

# Дефолт при отсутствии строки настроек — синхронен с серверным
# дефолтом колонки (миграция 0032) и константами роута настроек.
DEFAULT_CLUSTER_COUNT = 8

UNAVAILABLE_REASON = (
    "Граф документов недоступен: требуется дополнительный компонент. "
    "Установите orqion[graph]: pip install orqion[graph]"
)


async def _check_view_document_graph(session: AsyncSession, user: User) -> bool:
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities or "view_document_graph" in policy.capabilities


def _build_elements(
    documents: list[ClusteredDocument],
) -> tuple[list[DocumentGraphNode], list[DocumentGraphEdge]]:
    """Узлы-группы и узлы-документы с рёбрами принадлежности.

    Порядок детерминированный: группы по номеру, документы по имени
    файла (сортировка домена).
    """
    cluster_ids = sorted({d.cluster for d in documents})
    nodes = [
        DocumentGraphNode(
            id=f"cluster:{label}",
            label=f"Группа {number}",
            kind="cluster",
        )
        for number, label in enumerate(cluster_ids, start=1)
    ]
    edges: list[DocumentGraphEdge] = []
    for document in documents:
        nodes.append(
            DocumentGraphNode(
                id=f"doc:{document.document_id}",
                label=document.filename,
                kind="document",
                document_id=document.document_id,
            )
        )
        edges.append(
            DocumentGraphEdge(
                source=f"cluster:{document.cluster}",
                target=f"doc:{document.document_id}",
                kind="member",
            )
        )
    return nodes, edges


@router.get("/{corpus_id}/document-graph", response_model=DocumentGraphResponse)
async def get_document_graph(
    corpus_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> DocumentGraphResponse:
    if not await _check_view_document_graph(session, user):
        raise NotFound(
            constraint={"object": "document-graph", "reason": "view_document_graph required"},
            hint="Нет права на просмотр графа связей документов",
        )

    workspace_id = request.app.state.workspace_id
    corpus = (
        await session.execute(
            select(Corpus).where(Corpus.id == corpus_id, Corpus.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if corpus is None:
        raise NotFound(
            constraint={"object": "corpus", "id": corpus_id},
            hint="Корпус не найден или недоступен",
        )

    if not is_clustering_available():
        return DocumentGraphResponse(
            corpus_id=corpus_id,
            index_version_id=corpus.active_index_version_id,
            available=False,
            reason=UNAVAILABLE_REASON,
        )

    if corpus.active_index_version_id is None:
        return DocumentGraphResponse(
            corpus_id=corpus_id,
            index_version_id=None,
            available=True,
        )

    settings_row = (
        await session.execute(select(RagSettings).where(RagSettings.workspace_id == workspace_id))
    ).scalar_one_or_none()
    cluster_count = settings_row.cluster_count if settings_row else DEFAULT_CLUSTER_COUNT

    vector_store = request.app.state.vector_store
    build = await build_document_graph(session, vector_store, corpus, cluster_count)
    if build is None:
        return DocumentGraphResponse(
            corpus_id=corpus_id,
            index_version_id=None,
            available=True,
        )

    # Фиксирует кэш кластеризации в stats версии индекса (при промахе кэша).
    await session.commit()

    nodes, edges = _build_elements(build.documents)
    return DocumentGraphResponse(
        corpus_id=corpus_id,
        index_version_id=corpus.active_index_version_id,
        available=True,
        nodes=nodes,
        edges=edges,
        total_documents=build.total_documents,
        shown_documents=len(build.documents),
        truncated=build.truncated,
        cluster_count=cluster_count,
        from_cache=build.from_cache,
    )
