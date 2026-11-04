"""Hybrid retrieval with permission and validity filters (docs/05-rag-chat.md, "Retriever").

One Qdrant round trip runs two queries with the same filter: a dense-only query whose best
cosine score decides the threshold (RRF scores say nothing about absolute relevance), and
the hybrid query (dense + sparse prefetch, RRF fusion) that ranks the chunks.

Qdrant payloads may lag behind Postgres (outbox, see `app.ingest.indexer`), so chunks and
documents are re-read from Postgres with the same permission / validity conditions: a point
whose document became expired, was deleted or is not visible never reaches an answer, and
never counts for the threshold either.
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.core.config import settings
from app.db.models import LegalChunk, LegalDocument
from app.documents.service import visible_to_user_id
from app.ingest.embedder import Embedder, get_embedder
from app.ingest.indexer import DENSE, SPARSE
from app.ingest.schemas import normalize_so_hieu

EXPIRED_STATUS = "het_hieu_luc_toan_bo"
# Candidates per prefetch, relative to `top_k`.
PREFETCH_FACTOR = 4
# Case-insensitive: users often type "45/2019/qh14". Matches are normalized to upper case.
SO_HIEU_PATTERN = re.compile(r"\b\d{1,4}/\d{4}/[A-ZĐ\-]+\d*\b", re.IGNORECASE)
# Window overlap between consecutive chunks (chunker: ~WINDOW_OVERLAP_TOKENS tokens). A shorter
# common run of words is taken as coincidence, a longer one cannot be a carried overlap.
MIN_OVERLAP_WORDS = 5
MAX_OVERLAP_WORDS = 128
WORD = re.compile(r"\S+")

logger = logging.getLogger(__name__)


@dataclass
class RetrievalFilters:
    user_id: uuid.UUID
    include_expired: bool = False
    so_hieu_norm: list[str] | None = None
    doc_types: list[str] | None = None


@dataclass
class RetrievedChunk:
    chunk: LegalChunk  # `chunk.document` is loaded
    score: float  # RRF fusion score; only meaningful as a rank


@dataclass
class SourceBlock:
    """Chunks of one Điều of one document (or a single chunk outside any Điều), in text
    order: what the LLM reads as one numbered source."""

    document: LegalDocument
    chunks: list[LegalChunk]

    @property
    def dieu(self) -> str | None:
        return self.chunks[0].dieu

    @property
    def dieu_title(self) -> str | None:
        return self.chunks[0].dieu_title

    @property
    def text(self) -> str:
        first = self.chunks[0].text
        # Every part of a split Điều starts with its heading line again: show it once.
        heading = first.split("\n", 1)[0] + "\n"
        parts = [first]
        for previous, chunk in zip(self.chunks, self.chunks[1:], strict=False):
            text = chunk.text.removeprefix(heading) if self.dieu else chunk.text
            if chunk.ordinal == previous.ordinal + 1:
                text = _drop_overlap(previous.text, text)
                if text:
                    parts.append("\n" + text)
            else:
                # A gap between two retrieved parts of the same Điều is marked, not hidden.
                parts.append("\n[...]\n" + text)
        return "".join(parts)


@dataclass
class RetrievalResult:
    # Ranked best first; empty when nothing passed the threshold (refuse, do not call the LLM).
    chunks: list[RetrievedChunk]
    # Cosine score of the best dense match still visible in Postgres, None if there is none.
    best_dense_score: float | None
    # Số hiệu filter actually applied (given in the filters or found in the question).
    so_hieu_norm: list[str] | None = None
    blocks: list[SourceBlock] = field(init=False)

    def __post_init__(self) -> None:
        self.blocks = group_blocks(self.chunks)

    @property
    def found(self) -> bool:
        return bool(self.chunks)


def _drop_overlap(previous: str, text: str) -> str:
    """`text` without its leading words that repeat the end of `previous` (the window
    overlap of consecutive chunks)."""
    tail = previous.split()[-MAX_OVERLAP_WORDS:]
    words = list(WORD.finditer(text))
    for k in range(min(len(tail), len(words)), MIN_OVERLAP_WORDS - 1, -1):
        if tail[-k:] == [match.group() for match in words[:k]]:
            return text[words[k - 1].end() :].lstrip()
    return text


def find_so_hieu(query: str) -> list[str]:
    """Normalized document numbers ("100/2019/NĐ-CP") in the question, without duplicates."""
    found = (normalize_so_hieu(match.group()) for match in SO_HIEU_PATTERN.finditer(query))
    return list(dict.fromkeys(value for value in found if value))


def build_filter(filters: RetrievalFilters, so_hieu_norm: list[str] | None = None) -> qm.Filter:
    """Qdrant filter: permission always, then validity, số hiệu and document type."""
    must: list[qm.Condition] = [
        # Nested so that "at least one of" holds whatever else is in `must` (rule 5).
        qm.Filter(
            should=[
                qm.FieldCondition(key="source", match=qm.MatchValue(value="vbpl")),
                qm.FieldCondition(key="owner_id", match=qm.MatchValue(value=str(filters.user_id))),
            ]
        )
    ]
    if so_hieu_norm:
        must.append(qm.FieldCondition(key="so_hieu_norm", match=qm.MatchAny(any=so_hieu_norm)))
    if filters.doc_types:
        must.append(qm.FieldCondition(key="doc_type", match=qm.MatchAny(any=filters.doc_types)))
    must_not: list[qm.Condition] = []
    if not filters.include_expired:
        must_not.append(qm.FieldCondition(key="status", match=qm.MatchValue(value=EXPIRED_STATUS)))
    return qm.Filter(must=must, must_not=must_not or None)


def _document_conditions(filters: RetrievalFilters) -> ColumnElement[bool]:
    """The same conditions as `build_filter` (but số hiệu), checked against Postgres."""
    conditions = [visible_to_user_id(filters.user_id)]
    if not filters.include_expired:
        conditions.append(LegalDocument.status != EXPIRED_STATUS)
    if filters.doc_types:
        conditions.append(LegalDocument.doc_type.in_(filters.doc_types))
    return and_(*conditions)


def group_blocks(chunks: list[RetrievedChunk]) -> list[SourceBlock]:
    """Merge chunks of the same Điều of the same document into one block, placed at the
    rank of its best chunk and kept in text order. Chunks outside any Điều (preamble,
    appendix) stay alone."""
    blocks: list[SourceBlock] = []
    by_dieu: dict[tuple[uuid.UUID, str], SourceBlock] = {}
    for item in chunks:
        chunk = item.chunk
        if chunk.dieu is None:
            blocks.append(SourceBlock(chunk.document, [chunk]))
            continue
        key = (chunk.document_id, chunk.dieu)
        block = by_dieu.get(key)
        if block is None:
            block = by_dieu[key] = SourceBlock(chunk.document, [])
            blocks.append(block)
        block.chunks.append(chunk)
    for block in blocks:
        block.chunks.sort(key=lambda c: c.ordinal)
    return blocks


async def _existing_so_hieu(
    session: AsyncSession, candidates: list[str], filters: RetrievalFilters
) -> list[str]:
    """The candidates that name at least one indexed document the search could return.

    A document without chunks (ingestion pending or failed) would turn the filter into an
    empty search, so it does not count."""
    rows = await session.scalars(
        select(LegalDocument.so_hieu_norm)
        .where(
            LegalDocument.so_hieu_norm.in_(candidates),
            _document_conditions(filters),
            LegalDocument.chunks.any(),
        )
        .distinct()
    )
    found = set(rows)
    return [value for value in candidates if value in found]


async def retrieve(
    session: AsyncSession,
    qdrant: AsyncQdrantClient,
    query: str,
    filters: RetrievalFilters,
    top_k: int | None = None,
    *,
    min_score: float | None = None,
    embedder: Embedder | None = None,
) -> RetrievalResult:
    """Rank the chunks the user may see for `query`.

    `top_k` and `min_score` default to `RAG_TOP_K` / `RAG_MIN_SCORE`. Returns no chunks if
    the best dense cosine score is below `min_score`.
    """
    embedder = embedder or get_embedder()
    top_k = settings.rag_top_k if top_k is None else top_k
    min_score = settings.rag_min_score if min_score is None else min_score

    # The embedding runs in a worker thread: overlap it with the số hiệu lookup.
    embedding = asyncio.ensure_future(embedder.aembed_query(query))
    try:
        if filters.so_hieu_norm:
            so_hieu = [v for v in map(normalize_so_hieu, filters.so_hieu_norm) if v]
        else:
            candidates = find_so_hieu(query)
            so_hieu = await _existing_so_hieu(session, candidates, filters) if candidates else []
        vector = await embedding
    finally:
        embedding.cancel()  # no-op once done; avoids a dangling task if the lookup failed
    so_hieu = so_hieu or None

    sparse = qm.SparseVector(indices=list(vector.sparse), values=list(vector.sparse.values()))
    qfilter = build_filter(filters, so_hieu)
    candidates_limit = top_k * PREFETCH_FACTOR
    dense, hybrid = await qdrant.query_batch_points(
        settings.qdrant_collection,
        requests=[
            # Several candidates, not one: the best may be dropped by the Postgres check.
            qm.QueryRequest(
                query=vector.dense, using=DENSE, filter=qfilter, limit=candidates_limit
            ),
            qm.QueryRequest(
                prefetch=[
                    qm.Prefetch(
                        query=vector.dense, using=DENSE, filter=qfilter, limit=candidates_limit
                    ),
                    qm.Prefetch(query=sparse, using=SPARSE, filter=qfilter, limit=candidates_limit),
                ],
                query=qm.FusionQuery(fusion=qm.Fusion.RRF),
                # Over-fetch too: points dropped by the Postgres check are replaced by the
                # next ones instead of shrinking the result below `top_k`.
                limit=candidates_limit,
            ),
        ],
    )

    if not dense.points or dense.points[0].score < min_score:
        best = dense.points[0].score if dense.points else None
        logger.info("Retrieval below threshold: best dense score %s < %s", best, min_score)
        return RetrievalResult([], best, so_hieu)

    dense_scores = {uuid.UUID(str(point.id)): point.score for point in dense.points}
    scores = {uuid.UUID(str(point.id)): point.score for point in hybrid.points}
    ids = list(dense_scores.keys() | scores.keys())
    rows = await session.scalars(
        select(LegalChunk)
        .join(LegalChunk.document)
        .options(contains_eager(LegalChunk.document))
        .where(LegalChunk.id.in_(ids), _document_conditions(filters))
    )
    by_id = {chunk.id: chunk for chunk in rows}
    if len(by_id) < len(ids):
        # Points of a failed run, or a payload not yet synced (status, deletion).
        logger.info("Dropped %d points not matching Postgres", len(ids) - len(by_id))

    # Rule 3: the threshold only counts points that may actually reach the answer.
    best = max((s for chunk_id, s in dense_scores.items() if chunk_id in by_id), default=None)
    if best is None or best < min_score:
        logger.info("Retrieval below threshold: best visible dense score %s < %s", best, min_score)
        return RetrievalResult([], best, so_hieu)

    chunks = [
        RetrievedChunk(by_id[chunk_id], score)
        for chunk_id, score in scores.items()  # Qdrant's rank order
        if chunk_id in by_id
    ][:top_k]
    return RetrievalResult(chunks, best, so_hieu)
