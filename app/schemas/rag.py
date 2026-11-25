"""로컬 SQLite 하이브리드 RAG(FTS5 + sqlite-vec) 스키마."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class RagDocumentInfo(BaseModel):
    id: int
    title: str
    doc_type: str
    file_path: str = ""
    chunk_count: int = 0
    created_at: str = ""


class RagStatus(BaseModel):
    vec_available: bool
    current_model: str
    current_dim: int
    indexed_model: str
    indexed_dim: int
    needs_reindex: bool
    doc_count: int
    chunk_count: int
    documents: list[RagDocumentInfo] = Field(default_factory=list)


class RagIngestPathRequest(BaseModel):
    file_path: str
    title: str = ""
    doc_type: str = "reference"


class RagSearchRequest(BaseModel):
    query: str
    top_k: int = 5


class RagSearchHit(BaseModel):
    chunk_id: int
    doc_id: int
    doc_title: str
    doc_type: str
    content: str
    rrf_score: float
    fts_rank: int | None = None
    vec_rank: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
