"""Admin API schemas (docs/06-api.md, "Admin")."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class CrawlRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    updated_at: datetime
    stats: dict[str, Any]
    params: dict[str, Any]
    error: str | None


class CrawlRunList(BaseModel):
    items: list[CrawlRunOut]
    total: int


class CrawlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: dict[str, Any] | None = None


class CrawlStarted(BaseModel):
    crawl_run_id: uuid.UUID


class ReindexStarted(BaseModel):
    document_id: uuid.UUID


class DocumentCounts(BaseModel):
    total: int
    by_source: dict[str, int]
    by_status: dict[str, int]
    by_ingest_status: dict[str, int]


class QdrantStats(BaseModel):
    collection: str
    status: str
    points_count: int | None
    indexed_vectors_count: int | None
    segments_count: int | None


class AdminStats(BaseModel):
    documents: DocumentCounts
    chunks: int
    # None when Qdrant cannot be reached.
    qdrant: QdrantStats | None
    # Latest run of each job type (None: never ran).
    last_runs: dict[str, CrawlRunOut | None]
