"""Pydantic-схемы графа связей кода (Т-504)."""

from __future__ import annotations

from pydantic import BaseModel


class CodeGraphNode(BaseModel):
    id: str
    label: str
    # symbol | file | module
    kind: str
    file: str | None = None
    language: str | None = None


class CodeGraphEdge(BaseModel):
    source: str
    target: str
    # parent | import
    kind: str


class CodeGraphResponse(BaseModel):
    corpus_id: str
    index_version_id: str | None
    nodes: list[CodeGraphNode]
    edges: list[CodeGraphEdge]
    total_nodes: int
    shown_nodes: int
    truncated: bool
