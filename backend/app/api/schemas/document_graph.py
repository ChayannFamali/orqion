"""Pydantic-схемы графа связей документов (Т-505)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class DocumentGraphNode(BaseModel):
    id: str
    label: str
    kind: Literal["cluster", "document"]
    document_id: str | None = None


class DocumentGraphEdge(BaseModel):
    source: str
    target: str
    kind: Literal["member"]


class DocumentGraphResponse(BaseModel):
    corpus_id: str
    index_version_id: str | None
    available: bool
    reason: str | None = None
    nodes: list[DocumentGraphNode] = []
    edges: list[DocumentGraphEdge] = []
    total_documents: int = 0
    shown_documents: int = 0
    truncated: bool = False
    cluster_count: int | None = None
    from_cache: bool = False
