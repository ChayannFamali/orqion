"""GET /api/corpora/{corpus_id}/code-graph — граф связей кода (Т-504).

Read-only визуализация связей между чанками кода активной версии
индекса: рёбра «символ → родительский класс» (метаданные `parent`) и
«чанк → импортируемый модуль» (метаданные `imports`, пишутся в
метаданные при сборке начиная с этой задачи; для старых версий рёбер
импортов нет — без принудительной пересборки).

Доступ — отдельная способность ``view_code_graph`` по паттерну
``view_diagnostics``: гейт ``WILDCARD or "view_code_graph"``, без права
404; в посевные пресеты способность не добавляется.

Усечение по числу узлов — только явное: при превышении лимита в ответе
``truncated=true`` и полное число узлов, интерфейс показывает
«показано N из M узлов».
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.code_graph import CodeGraphEdge, CodeGraphNode, CodeGraphResponse
from app.auth.dependencies import current_user
from app.db.models import Chunk, Corpus, User
from app.db.session import get_session
from app.errors import NotFound
from app.policy.models import WILDCARD
from app.policy.resolve import resolve_policy

router = APIRouter(
    prefix="/api/corpora",
    tags=["corpora"],
    dependencies=[Depends(current_user)],
)

MAX_NODES = 300


async def _check_view_code_graph(session: AsyncSession, user: User) -> bool:
    policy = await resolve_policy(session, user)
    return WILDCARD in policy.capabilities or "view_code_graph" in policy.capabilities


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _build_graph(
    chunks: list[Chunk],
) -> tuple[list[CodeGraphNode], list[CodeGraphEdge], int]:
    """Строит полный граф по чанкам и возвращает (узлы, рёбра, всего узлов).

    Узлы-чанки идут первыми в исходном порядке (стабильное усечение);
    синтетические узлы (родители без собственного чанка, модули импортов)
    добавляются по мере появления рёбер.
    """
    nodes: list[CodeGraphNode] = []
    edges: list[CodeGraphEdge] = []
    node_ids: set[str] = set()

    chunk_node_id: dict[str, str] = {}
    symbol_index: dict[tuple[str, str], str] = {}

    for chunk in chunks:
        meta = chunk.meta or {}
        file_path = str(meta.get("file", "") or "")
        symbol = meta.get("symbol")
        node_id = f"chunk:{chunk.id}"
        label = str(symbol) if symbol else (_basename(file_path) or "фрагмент")
        nodes.append(
            CodeGraphNode(
                id=node_id,
                label=label,
                kind="symbol" if symbol else "file",
                file=file_path or None,
                language=str(meta.get("language")) if meta.get("language") else None,
            )
        )
        node_ids.add(node_id)
        chunk_node_id[chunk.id] = node_id
        if symbol and file_path:
            symbol_index.setdefault((file_path, str(symbol)), node_id)

    def _ensure_synthetic(node_id: str, label: str, kind: str) -> None:
        if node_id in node_ids:
            return
        nodes.append(CodeGraphNode(id=node_id, label=label, kind=kind))
        node_ids.add(node_id)

    for chunk in chunks:
        meta = chunk.meta or {}
        file_path = str(meta.get("file", "") or "")
        source_id = chunk_node_id[chunk.id]

        parent = meta.get("parent")
        if parent:
            target_id = symbol_index.get((file_path, str(parent)))
            if target_id is None:
                target_id = f"parent:{file_path}:{parent}"
                _ensure_synthetic(target_id, str(parent), "symbol")
            edges.append(CodeGraphEdge(source=source_id, target=target_id, kind="parent"))

        imports = meta.get("imports")
        if isinstance(imports, list):
            for imp in imports:
                name = str(imp)
                target_id = f"module:{name}"
                _ensure_synthetic(target_id, name, "module")
                edges.append(CodeGraphEdge(source=source_id, target=target_id, kind="import"))

    return nodes, edges, len(nodes)


def _truncate(
    nodes: list[CodeGraphNode],
    edges: list[CodeGraphEdge],
    limit: int,
) -> tuple[list[CodeGraphNode], list[CodeGraphEdge], bool]:
    """Усечение только явное: лимит по узлам-чанкам, синтетика — за ними."""
    chunk_nodes = [n for n in nodes if n.kind in ("symbol", "file")]
    if len(chunk_nodes) <= limit:
        return nodes, edges, False

    kept_chunk_ids = {n.id for n in chunk_nodes[:limit]}
    kept_nodes = [n for n in chunk_nodes[:limit]]
    kept_edges = [e for e in edges if e.source in kept_chunk_ids]
    needed_targets = {e.target for e in kept_edges}
    for n in nodes:
        if n.kind in ("symbol", "file"):
            continue
        if n.id in needed_targets:
            kept_nodes.append(n)
    return kept_nodes, kept_edges, True


@router.get("/{corpus_id}/code-graph", response_model=CodeGraphResponse)
async def get_code_graph(
    corpus_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> CodeGraphResponse:
    if not await _check_view_code_graph(session, user):
        raise NotFound(
            constraint={"object": "code-graph", "reason": "view_code_graph required"},
            hint="Нет права на просмотр графа связей кода",
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

    if corpus.active_index_version_id is None:
        return CodeGraphResponse(
            corpus_id=corpus_id,
            index_version_id=None,
            nodes=[],
            edges=[],
            total_nodes=0,
            shown_nodes=0,
            truncated=False,
        )

    rows = (
        (
            await session.execute(
                select(Chunk)
                .where(
                    Chunk.workspace_id == workspace_id,
                    Chunk.index_version_id == corpus.active_index_version_id,
                )
                .order_by(Chunk.document_id, Chunk.ordinal)
            )
        )
        .scalars()
        .all()
    )

    nodes, edges, total = _build_graph(list(rows))
    shown_nodes, shown_edges, truncated = _truncate(nodes, edges, MAX_NODES)
    return CodeGraphResponse(
        corpus_id=corpus_id,
        index_version_id=corpus.active_index_version_id,
        nodes=shown_nodes,
        edges=shown_edges,
        total_nodes=total,
        shown_nodes=len(shown_nodes),
        truncated=truncated,
    )
