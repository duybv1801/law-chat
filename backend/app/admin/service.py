"""Admin business logic: stats, crawl runs, manual re-index of one document."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import status
from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import AdminStats, CrawlRunList, CrawlRunOut, DocumentCounts, QdrantStats
from app.core.config import settings
from app.core.errors import AppError
from app.crawler.vbpl_client import VbplClient
from app.db.models import CRAWL_JOB_TYPES, CrawlRun, LegalChunk, LegalDocument
from app.db.session import SessionFactory
from app.documents.service import find_upload_file, ingest_upload
from app.ingest.indexer import index_document

QDRANT_TIMEOUT_S = 3.0

logger = logging.getLogger(__name__)

# Replaced in tests by a fake vbpl.vn client (an async context manager).
vbpl_client_factory: Any = VbplClient


# --- Stats ------------------------------------------------------------------------------


async def _count_by(session: AsyncSession, column: Any) -> dict[str, int]:
    rows = await session.execute(select(column, func.count()).group_by(column).order_by(column))
    return {key: count for key, count in rows.all()}


async def _qdrant_stats(qdrant: AsyncQdrantClient) -> QdrantStats | None:
    name = settings.qdrant_collection
    try:
        info = await asyncio.wait_for(qdrant.get_collection(name), timeout=QDRANT_TIMEOUT_S)
    except Exception as exc:
        logger.warning("Admin stats: Qdrant unavailable: %s", type(exc).__name__)
        return None
    return QdrantStats(
        collection=name,
        status=str(getattr(info.status, "value", info.status)),
        points_count=info.points_count,
        indexed_vectors_count=info.indexed_vectors_count,
        segments_count=info.segments_count,
    )


async def last_runs(session: AsyncSession) -> dict[str, CrawlRunOut | None]:
    result: dict[str, CrawlRunOut | None] = {}
    for job_type in CRAWL_JOB_TYPES:
        row = await session.scalar(
            select(CrawlRun)
            .where(CrawlRun.job_type == job_type)
            .order_by(CrawlRun.started_at.desc())
            .limit(1)
        )
        result[job_type] = CrawlRunOut.model_validate(row) if row is not None else None
    return result


async def stats(session: AsyncSession, qdrant: AsyncQdrantClient) -> AdminStats:
    # Qdrant (up to QDRANT_TIMEOUT_S) runs while Postgres is counted.
    qdrant_stats = asyncio.create_task(_qdrant_stats(qdrant))
    by_source = await _count_by(session, LegalDocument.source)
    documents = DocumentCounts(
        total=sum(by_source.values()),
        by_source=by_source,
        by_status=await _count_by(session, LegalDocument.status),
        by_ingest_status=await _count_by(session, LegalDocument.ingest_status),
    )
    chunks = await session.scalar(select(func.count()).select_from(LegalChunk)) or 0
    return AdminStats(
        documents=documents,
        chunks=chunks,
        qdrant=await qdrant_stats,
        last_runs=await last_runs(session),
    )


# --- Crawl runs -------------------------------------------------------------------------


async def list_crawl_runs(
    session: AsyncSession, *, job_type: str | None, page: int, page_size: int
) -> CrawlRunList:
    query = select(CrawlRun)
    if job_type:
        query = query.where(CrawlRun.job_type == job_type)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(
        query.order_by(CrawlRun.started_at.desc(), CrawlRun.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return CrawlRunList(items=[CrawlRunOut.model_validate(r) for r in rows], total=total or 0)


# --- Re-index ---------------------------------------------------------------------------


def _not_found() -> AppError:
    return AppError(status.HTTP_404_NOT_FOUND, "not_found", "Không tìm thấy văn bản.")


async def check_reindex(session: AsyncSession, document_id: uuid.UUID) -> LegalDocument:
    """The document to re-index, or an `AppError` explaining why it cannot be."""
    doc = await session.get(LegalDocument, document_id)
    if doc is None:
        raise _not_found()
    if doc.ingest_status in ("pending", "processing"):
        raise AppError(
            status.HTTP_409_CONFLICT,
            "ingest_in_progress",
            "Văn bản đang được xử lý, vui lòng thử lại sau.",
        )
    if doc.source == "user_upload" and (
        doc.owner_id is None or find_upload_file(doc.owner_id, doc.id) is None
    ):
        raise AppError(
            status.HTTP_409_CONFLICT,
            "file_missing",
            "Không còn file gốc của văn bản này để xử lý lại.",
        )
    return doc


async def reindex_upload(
    session_factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    document_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> None:
    """Background task: re-parse the stored file and re-embed it."""
    path = find_upload_file(owner_id, document_id)
    if path is None:
        logger.warning("Re-index of upload %s: the file is gone", document_id)
        return
    await ingest_upload(session_factory, qdrant, document_id, path, force=True)


async def reindex_vbpl(
    session_factory: SessionFactory, qdrant: AsyncQdrantClient, document_id: uuid.UUID
) -> None:
    """Background task: fetch the document again from vbpl.vn (through the shared rate
    limiter) and re-embed it with its fresh metadata. Never raises: if vbpl.vn fails or
    no longer has the full text, the indexed version is kept."""
    try:
        async with session_factory() as session:
            doc = await session.get(LegalDocument, document_id)
            if doc is None or doc.source_id is None:
                return
            source_id, url, scope = doc.source_id, doc.source_url, doc.scope
        async with vbpl_client_factory() as client:
            fetched = await client.get_document(source_id, url)
        if fetched.text is None:
            logger.warning("Re-index of %s: no HTML full text on vbpl.vn, kept as is", source_id)
            return
        new = fetched.to_document_in(crawled_at=datetime.now(UTC), scope=scope)
        async with session_factory() as session:
            await index_document(session, qdrant, new, fetched.text, force=True)
        logger.info("Re-indexed vbpl document %s (%s)", source_id, document_id)
    except Exception as exc:
        # The indexer marks the document failed itself when it got that far.
        logger.warning("Re-index of document %s failed: %s", document_id, type(exc).__name__)
