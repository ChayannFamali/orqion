"""Т-504: построение и усечение графа связей кода — чистые функции."""

from __future__ import annotations

from app.api.routes.code_graph import _build_graph, _truncate
from app.db.models import Chunk


def _chunk(chunk_id: str, meta: dict[str, object]) -> Chunk:
    return Chunk(
        id=chunk_id,
        workspace_id="ws",
        index_version_id="iv",
        document_id="doc",
        ordinal=0,
        text="x",
        meta=meta,
    )


def test_build_graph_nodes_and_parent_edge() -> None:
    """Узлы-символы + ребро к родителю, который сам является чанком."""
    chunks = [
        _chunk("c1", {"file": "a.py", "language": "python", "symbol": "MyClass"}),
        _chunk(
            "c2",
            {"file": "a.py", "language": "python", "symbol": "my_method", "parent": "MyClass"},
        ),
    ]
    nodes, edges, total = _build_graph(chunks)

    assert total == 2
    assert {n.id for n in nodes} == {"chunk:c1", "chunk:c2"}
    labels = {n.id: n.label for n in nodes}
    assert labels["chunk:c1"] == "MyClass"
    assert labels["chunk:c2"] == "my_method"

    parent_edges = [e for e in edges if e.kind == "parent"]
    assert len(parent_edges) == 1
    assert parent_edges[0].source == "chunk:c2"
    assert parent_edges[0].target == "chunk:c1"  # родитель нашёлся среди чанков


def test_build_graph_synthetic_parent_when_no_chunk() -> None:
    chunks = [
        _chunk("c1", {"file": "a.py", "symbol": "my_method", "parent": "Base"}),
    ]
    nodes, edges, total = _build_graph(chunks)

    assert total == 2  # чанк + синтетический родитель
    parent_edges = [e for e in edges if e.kind == "parent"]
    assert parent_edges[0].target == "parent:a.py:Base"
    synthetic = [n for n in nodes if n.id == "parent:a.py:Base"]
    assert synthetic[0].label == "Base"
    assert synthetic[0].kind == "symbol"


def test_build_graph_import_edges_to_module_nodes() -> None:
    chunks = [
        _chunk("c1", {"file": "a.py", "symbol": "f", "imports": ["os", "app.utils"]}),
    ]
    nodes, edges, total = _build_graph(chunks)

    assert total == 3  # чанк + два модуля
    import_edges = [e for e in edges if e.kind == "import"]
    assert {e.target for e in import_edges} == {"module:os", "module:app.utils"}
    module_nodes = {n.id: n.label for n in nodes if n.kind == "module"}
    assert module_nodes == {"module:os": "os", "module:app.utils": "app.utils"}


def test_build_graph_chunk_without_symbol_is_file_node() -> None:
    chunks = [_chunk("c1", {"file": "docs/readme.txt"})]
    nodes, _, _ = _build_graph(chunks)
    assert nodes[0].kind == "file"
    assert nodes[0].label == "readme.txt"


def test_truncate_no_truncation_below_limit() -> None:
    chunks = [_chunk(f"c{i}", {"file": "a.py", "symbol": f"s{i}"}) for i in range(5)]
    nodes, edges, total = _build_graph(chunks)
    kept_nodes, _kept_edges, truncated = _truncate(nodes, edges, limit=300)
    assert truncated is False
    assert len(kept_nodes) == total


def test_truncate_caps_chunk_nodes_explicitly() -> None:
    """Усечение явное: лимит по узлам-чанкам, лишние рёбра отбрасываются,
    нужная синтетика остаётся."""
    chunks = [
        _chunk("small", {"file": "a.py", "symbol": "f", "imports": ["os"]}),
    ] + [_chunk(f"c{i}", {"file": "a.py", "symbol": f"s{i}"}) for i in range(4)]
    nodes, edges, total = _build_graph(chunks)
    assert total == 6  # 5 чанков + модуль os

    kept_nodes, kept_edges, truncated = _truncate(nodes, edges, limit=3)
    assert truncated is True
    kept_chunk_nodes = [n for n in kept_nodes if n.kind == "symbol"]
    assert len(kept_chunk_nodes) == 3
    # Все рёбра усечённых чанков отброшены
    kept_ids = {n.id for n in kept_nodes}
    assert all(e.source in kept_ids for e in kept_edges)


def test_truncate_keeps_synthetic_for_kept_chunks() -> None:
    """Синтетический узел импорта сохраняется, если его чанк остался."""
    chunks = [
        _chunk("keeper", {"file": "a.py", "symbol": "f", "imports": ["json"]}),
    ] + [_chunk(f"c{i}", {"file": "a.py", "symbol": f"s{i}"}) for i in range(5)]
    nodes, edges, _ = _build_graph(chunks)

    kept_nodes, kept_edges, truncated = _truncate(nodes, edges, limit=1)
    assert truncated is True
    kept_ids = {n.id for n in kept_nodes}
    # Чанк-«хранитель» первый по порядку построения — остаётся
    assert "chunk:keeper" in kept_ids
    # Его ребро импорта и модуль-цель сохранены
    assert any(e.kind == "import" and e.target == "module:json" for e in kept_edges)
    assert "module:json" in kept_ids
