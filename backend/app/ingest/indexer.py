"""Write documents/chunks to Postgres and points to Qdrant.

The only module that writes to Qdrant (docs/02-data-model.md, "Đồng bộ Postgres → Qdrant").
Postgres is the source of truth; point payloads are copies of document columns used for
filtering. Every function commits the session it is given (which must be created with
`expire_on_commit=False`, like `SessionLocal`). Work is done in a SAVEPOINT so a failure only
expires the document it touched; only a failed COMMIT rolls back (and expires) the session.

Writes to one document are serialized by its row lock (`SELECT ... FOR UPDATE`), taken by
the metadata update, the payload sync, the delete and the final swap of an indexing run.
Embedding itself (minutes on CPU) runs without the lock; the swap then re-reads the row, so
a metadata update or a delete that happened meanwhile is not lost.

Metadata updates commit to Postgres first and set `payload_dirty` (outbox); the payload is
copied to Qdrant right after, or by `sync_pending_payloads` if Qdrant was down.
"""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from typing import Any

import anyio
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import LegalChunk, LegalDocument
from app.ingest.chunker import Chunk, chunk_document
from app.ingest.embedder import Embedder, EmbeddingResult, get_embedder
from app.ingest.schemas import DocumentMetadata, LegalDocumentIn, normalize_so_hieu

UPSERT_BATCH_SIZE = 64
DENSE = "dense"
SPARSE = "sparse"
# Collection metadata key holding the embedding model name (checked at startup).
MODEL_METADATA_KEY = "embedding_model"
# Upper bound for the cleanup after a failed or cancelled indexing run.
CLEANUP_TIMEOUT_S = 30

KEYWORD_INDEXES = (
    "document_id",
    "source",
    "owner_id",
    "so_hieu_norm",
    "doc_type",
    "status",
    "scope",
)
DATETIME_INDEXES = ("effective_date", "issued_date")
# Document columns copied into every point payload.
PAYLOAD_COLUMNS = frozenset(
    {
        "source",
        "owner_id",
        "so_hieu_norm",
        "doc_type",
        "status",
        "scope",
        "issued_date",
        "effective_date",
    }
)
# Document columns written into every chunk's `embed_text` (and so into its vectors).
EMBEDDED_COLUMNS = ("title", "so_hieu")

EMPTY_DOCUMENT_MESSAGE = "Văn bản không có nội dung để lập chỉ mục."
INDEX_FAILED_MESSAGE = "Lỗi khi lập chỉ mục văn bản. Vui lòng thử lại sau."

logger = logging.getLogger(__name__)

# Collections already checked by `ensure_collection` in this process.
_ready_collections: set[str] = set()


class CollectionMismatchError(RuntimeError):
    """The Qdrant collection was built with another embedding model or vector size."""


class IndexingError(Exception):
    """Indexing failed for a reason the user can act on; `user_message` goes to `ingest_error`."""

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = user_message


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _collection() -> str:
    return settings.qdrant_collection


def _document_filter(document_id: uuid.UUID) -> qm.Filter:
    return qm.Filter(
        must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=str(document_id)))]
    )


def _payload_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _payload(values: dict[str, Any]) -> dict[str, Any]:
    # NULL columns are left out, never stored as null: Qdrant's set_payload removes a key set
    # to null, so this keeps new and updated points alike. Filter "no value" with IsEmpty.
    return {key: _payload_value(value) for key, value in values.items() if value is not None}


def document_payload(doc: LegalDocument) -> dict[str, Any]:
    """Payload fields shared by all points of a document."""
    return _payload({"document_id": doc.id} | {c: getattr(doc, c) for c in PAYLOAD_COLUMNS})


async def ensure_collection(qdrant: AsyncQdrantClient, embedder: Embedder | None = None) -> None:
    """Create the collection and payload indexes if missing; refuse a collection built
    with another embedding model (re-indexing is never automatic)."""
    embedder = embedder or get_embedder()
    name = _collection()
    if not await qdrant.collection_exists(name):
        try:
            await qdrant.create_collection(
                name,
                vectors_config={
                    DENSE: qm.VectorParams(
                        size=embedder.dim, distance=qm.Distance.COSINE, on_disk=True
                    )
                },
                sparse_vectors_config={
                    SPARSE: qm.SparseVectorParams(index=qm.SparseIndexParams(on_disk=True))
                },
                quantization_config=qm.ScalarQuantization(
                    scalar=qm.ScalarQuantizationConfig(type=qm.ScalarType.INT8, always_ram=True)
                ),
                metadata={MODEL_METADATA_KEY: embedder.model_name},
            )
            logger.info("Created Qdrant collection %s for model %s", name, embedder.model_name)
        except Exception:
            # Another process (uvicorn, the crawler CLI) may have created it meanwhile.
            if not await qdrant.collection_exists(name):
                raise

    info = await qdrant.get_collection(name)
    stored_model = (info.config.metadata or {}).get(MODEL_METADATA_KEY)
    if stored_model is None and (await qdrant.count(name, exact=True)).count == 0:
        # Created before the model name was stored (pre-T10) and still empty: adopt it.
        await qdrant.update_collection(name, metadata={MODEL_METADATA_KEY: embedder.model_name})
        stored_model = embedder.model_name
    if stored_model != embedder.model_name:
        raise CollectionMismatchError(
            f"Qdrant collection {name!r} was indexed with embedding model {stored_model!r}, "
            f"but EMBEDDING_MODEL is {embedder.model_name!r}. Use another QDRANT_COLLECTION "
            "or re-index; the app never re-indexes on its own."
        )
    vectors = info.config.params.vectors
    dense = vectors.get(DENSE) if isinstance(vectors, dict) else None
    if dense is None or dense.size != embedder.dim:
        raise CollectionMismatchError(
            f"Qdrant collection {name!r} has no {DENSE!r} vector of size {embedder.dim}."
        )

    existing = info.payload_schema or {}
    indexes = [(f, qm.PayloadSchemaType.KEYWORD) for f in KEYWORD_INDEXES]
    indexes += [(f, qm.PayloadSchemaType.DATETIME) for f in DATETIME_INDEXES]
    for field, schema in indexes:
        if field not in existing:
            # Idempotent in Qdrant: a concurrent creation by another process is fine.
            await qdrant.create_payload_index(name, field, field_schema=schema, wait=True)
    _ready_collections.add(name)


async def _ensure_ready(qdrant: AsyncQdrantClient, embedder: Embedder) -> None:
    """`ensure_collection` once per process; covers a Qdrant that was down at startup."""
    if _collection() not in _ready_collections:
        await ensure_collection(qdrant, embedder)


async def _delete_points(qdrant: AsyncQdrantClient, document_id: uuid.UUID) -> None:
    await qdrant.delete(
        _collection(),
        points_selector=qm.FilterSelector(filter=_document_filter(document_id)),
        wait=True,
    )


async def _delete_point_ids(qdrant: AsyncQdrantClient, ids: Sequence[uuid.UUID]) -> None:
    if ids:
        await qdrant.delete(
            _collection(),
            points_selector=qm.PointIdsList(points=[str(i) for i in ids]),
            wait=True,
        )


async def _sync_payload(qdrant: AsyncQdrantClient, doc: LegalDocument) -> None:
    """Copy every payload column of `doc` to its points, in one request."""
    values = {c: getattr(doc, c) for c in PAYLOAD_COLUMNS}
    points = _document_filter(doc.id)
    operations: list[qm.UpdateOperation] = [
        qm.SetPayloadOperation(set_payload=qm.SetPayload(payload=_payload(values), filter=points))
    ]
    cleared = sorted(c for c, value in values.items() if value is None)
    if cleared:
        operations.append(
            qm.DeletePayloadOperation(delete_payload=qm.DeletePayload(keys=cleared, filter=points))
        )
    await qdrant.batch_update_points(_collection(), update_operations=operations, wait=True)


async def _lock_document(session: AsyncSession, document_id: uuid.UUID) -> LegalDocument | None:
    """Fresh copy of the row, locked until the end of the transaction (or SAVEPOINT)."""
    return await session.scalar(
        select(LegalDocument)
        .where(LegalDocument.id == document_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except BaseException:
        await session.rollback()
        raise


def _is_current(doc: LegalDocument, text_hash: str, embedder: Embedder) -> bool:
    """True if the document is already indexed from this text with this model."""
    return (
        doc.ingest_status == "done"
        and doc.content_hash == text_hash
        and doc.embedding_model == embedder.model_name
    )


async def _index(
    session: AsyncSession,
    qdrant: AsyncQdrantClient,
    doc: LegalDocument,
    text: str,
    text_hash: str,
    embedder: Embedder,
) -> None:
    """Build new chunks and points of `doc` (already added to the session) from `text`
    and swap them in for the previous ones.

    The previous version stays searchable until the swap commits. On failure it is kept
    (with its `content_hash`), the new points are removed, the document is marked `failed`
    with a user-facing `ingest_error`, and the exception is re-raised.
    """
    doc.ingest_status = "processing"
    doc.ingest_error = None
    await session.commit()
    document_id = doc.id
    embedded = {c: getattr(doc, c) for c in EMBEDDED_COLUMNS}
    chunk_ids: list[uuid.UUID] = []

    try:
        await _ensure_ready(qdrant, embedder)
        chunks = await asyncio.to_thread(chunk_document, text, **embedded)
        if not chunks:
            raise IndexingError("empty_document", EMPTY_DOCUMENT_MESSAGE)
        vectors = await embedder.aembed_documents([chunk.embed_text for chunk in chunks])
        chunk_ids = [uuid.uuid4() for _ in chunks]
        old_ids = await _swap(
            session, qdrant, document_id, embedded, chunks, vectors, chunk_ids, text_hash, embedder
        )
    except (Exception, asyncio.CancelledError) as exc:
        user_message = exc.user_message if isinstance(exc, IndexingError) else INDEX_FAILED_MESSAGE
        logger.warning(
            "Indexing document %s failed: %s", document_id, type(exc).__name__, exc_info=exc
        )
        # Shielded: once a task is cancelled (anyio / Starlette), every await would raise again.
        # An embedding thread still running cannot be stopped; it finishes on its own.
        with anyio.move_on_after(CLEANUP_TIMEOUT_S, shield=True):
            await _mark_failed(session, qdrant, doc, chunk_ids, user_message)
        raise

    try:
        await _delete_point_ids(qdrant, old_ids)
    except Exception:
        # Without chunk rows they never reach an answer; the next swap removes them.
        logger.exception("Could not delete replaced points of document %s", document_id)
    logger.info("Indexed document %s: %d chunks", document_id, len(chunks))


async def _swap(
    session: AsyncSession,
    qdrant: AsyncQdrantClient,
    document_id: uuid.UUID,
    embedded: dict[str, Any],
    chunks: list[Chunk],
    vectors: list[EmbeddingResult],
    chunk_ids: list[uuid.UUID],
    text_hash: str,
    embedder: Embedder,
) -> list[uuid.UUID]:
    """Under the row lock: upsert the new points, replace the chunk rows and mark the
    document `done`. Return the ids of the replaced chunks, whose points are left for the
    caller to delete once committed."""
    async with session.begin_nested():
        row = await _lock_document(session, document_id)
        if row is None:
            raise LookupError(f"document {document_id} was deleted while indexing")
        if {c: getattr(row, c) for c in EMBEDDED_COLUMNS} != embedded:
            # Another index_document changed the title / số hiệu meanwhile: its run wins.
            raise RuntimeError(f"document {document_id} was renamed while indexing")

        old_ids = list(
            await session.scalars(
                select(LegalChunk.id).where(LegalChunk.document_id == document_id)
            )
        )
        # Points of earlier runs that died before cleaning up (neither old nor new).
        await qdrant.delete(
            _collection(),
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=_document_filter(document_id).must,
                    must_not=[qm.HasIdCondition(has_id=[str(i) for i in old_ids + chunk_ids])],
                )
            ),
            wait=True,
        )
        # From the locked row: includes metadata updated while embedding.
        base_payload = document_payload(row)
        points = [
            qm.PointStruct(
                id=str(chunk_id),
                vector={
                    DENSE: vector.dense,
                    SPARSE: qm.SparseVector(
                        indices=list(vector.sparse), values=list(vector.sparse.values())
                    ),
                },
                payload=base_payload | _payload({"dieu": chunk.dieu, "ordinal": chunk.ordinal}),
            )
            for chunk_id, chunk, vector in zip(chunk_ids, chunks, vectors, strict=True)
        ]
        for start in range(0, len(points), UPSERT_BATCH_SIZE):
            await qdrant.upsert(
                _collection(), points=points[start : start + UPSERT_BATCH_SIZE], wait=True
            )

        await session.execute(delete(LegalChunk).where(LegalChunk.document_id == document_id))
        session.add_all(
            LegalChunk(id=chunk_id, document_id=document_id, **asdict(chunk))
            for chunk_id, chunk in zip(chunk_ids, chunks, strict=True)
        )
        row.content_hash = text_hash
        row.ingest_status = "done"
        row.ingest_error = None
        row.embedding_model = embedder.model_name
        row.payload_dirty = False  # the new points carry the payload of the locked row
    await _commit(session)
    return old_ids


async def _mark_failed(
    session: AsyncSession,
    qdrant: AsyncQdrantClient,
    doc: LegalDocument,
    new_ids: list[uuid.UUID],
    user_message: str,
) -> None:
    """Remove the points of the failed run and mark the document `failed`. The previous
    chunks and points, if any, stay searchable."""
    document_id = doc.id
    try:
        await _delete_point_ids(qdrant, new_ids)
    except Exception:
        # Without chunk rows they never reach an answer; the next swap removes them.
        logger.exception("Could not delete points of failed document %s", document_id)

    statement = (
        update(LegalDocument)
        .where(LegalDocument.id == document_id)
        .values(
            ingest_status="failed",
            ingest_error=user_message,
            updated_at=func.clock_timestamp(),  # Core UPDATE skips the ORM `onupdate`
        )
        .returning(LegalDocument.id)
        .execution_options(synchronize_session=False)
    )
    for attempt in range(2):
        try:
            found = await session.scalar(statement)
            await session.commit()
            if found is not None:
                await session.refresh(doc)  # expired by the SAVEPOINT rollback
            return
        except Exception:
            # e.g. the session was left mid-transaction by a cancelled query.
            await session.rollback()
            if attempt:
                logger.exception("Could not mark document %s as failed", document_id)


async def index_document(
    session: AsyncSession,
    qdrant: AsyncQdrantClient,
    doc: LegalDocumentIn,
    text: str,
    *,
    owner_id: uuid.UUID | None = None,
    embedder: Embedder | None = None,
) -> uuid.UUID:
    """Create or update a document from its metadata and normalized text; return its id.

    A document with the same (`source`, `source_id`) is updated in place: if it is already
    indexed from the same text, title and số hiệu with the current model only its metadata
    changes. Uploads (`source_id=None`) always create a new document.
    """
    embedder = embedder or get_embedder()
    text_hash = content_hash(text)
    metadata = doc.model_dump(exclude={"source", "source_id"})

    existing = None
    if doc.source_id is not None:
        existing = await session.scalar(
            select(LegalDocument).where(
                LegalDocument.source == doc.source, LegalDocument.source_id == doc.source_id
            )
        )
    if (
        existing is not None
        and _is_current(existing, text_hash, embedder)
        and all(getattr(existing, c) == metadata[c] for c in EMBEDDED_COLUMNS)
    ):
        await update_document_metadata(session, qdrant, existing.id, **metadata)
        return existing.id

    if existing is None:
        row = LegalDocument(
            id=uuid.uuid4(), source=doc.source, source_id=doc.source_id, owner_id=owner_id
        )
        session.add(row)
    else:
        row = existing
    _apply_metadata(row, metadata)
    await _index(session, qdrant, row, text, text_hash, embedder)
    return row.id


async def reindex_document(
    session: AsyncSession,
    qdrant: AsyncQdrantClient,
    document_id: uuid.UUID,
    text: str,
    *,
    embedder: Embedder | None = None,
) -> None:
    """(Re)build chunks and points of an existing document, e.g. an upload created as
    `pending`. No-op if it is already indexed from the same text with the current model."""
    embedder = embedder or get_embedder()
    doc = await session.get(LegalDocument, document_id)
    if doc is None:
        raise LookupError(f"document {document_id} not found")
    text_hash = content_hash(text)
    if _is_current(doc, text_hash, embedder):
        return
    await _index(session, qdrant, doc, text, text_hash, embedder)


def _apply_metadata(doc: LegalDocument, fields: dict[str, Any]) -> set[str]:
    """Set changed columns on `doc`; return the names of the columns that changed."""
    if "so_hieu" in fields:
        fields = {**fields, "so_hieu_norm": normalize_so_hieu(fields["so_hieu"])}
    changed = set()
    for column, value in fields.items():
        if getattr(doc, column) != value:
            setattr(doc, column, value)
            changed.add(column)
    return changed


async def update_document_metadata(
    session: AsyncSession, qdrant: AsyncQdrantClient, document_id: uuid.UUID, **fields: Any
) -> None:
    """Update metadata columns (see `DocumentMetadata`) in Postgres, then copy the payload
    fields to the document's points.

    Raises `ValueError` on an unknown column, or on a new `title` / `so_hieu`: they are
    embedded in the chunks, so they change only with `index_document`, which re-embeds.
    Postgres commits first and marks the document `payload_dirty` (outbox); if Qdrant
    fails, `sync_pending_payloads` retries later. Qdrant may lag behind, never diverge.
    """
    unknown = set(fields) - set(DocumentMetadata.model_fields)
    if unknown:
        raise ValueError(f"not a document metadata field: {', '.join(sorted(unknown))}")

    try:
        async with session.begin_nested():
            doc = await _lock_document(session, document_id)
            if doc is None:
                raise LookupError(f"document {document_id} not found")
            renamed = [c for c in EMBEDDED_COLUMNS if c in fields and getattr(doc, c) != fields[c]]
            if renamed:
                raise ValueError(
                    f"{', '.join(renamed)} is embedded in the chunks: re-index with "
                    "index_document instead"
                )
            if _apply_metadata(doc, fields) & PAYLOAD_COLUMNS:
                doc.payload_dirty = True
        await _commit(session)
    except Exception:
        await _reload(session, document_id)
        raise
    if doc.payload_dirty:
        await sync_document_payload(session, qdrant, document_id)


async def sync_document_payload(
    session: AsyncSession, qdrant: AsyncQdrantClient, document_id: uuid.UUID
) -> bool:
    """Copy the payload of a `payload_dirty` document to its points and clear the flag.

    Always writes the whole payload from the locked row, so a retry is idempotent and
    never writes an older state over a newer one. Returns False if Qdrant or Postgres
    failed: the flag stays set for the next retry.
    """
    try:
        async with session.begin_nested():
            doc = await _lock_document(session, document_id)
            if doc is not None and doc.payload_dirty:
                await _sync_payload(qdrant, doc)
                doc.payload_dirty = False
        await _commit(session)
    except Exception as exc:
        logger.warning("Payload sync of document %s deferred: %s", document_id, type(exc).__name__)
        await _reload(session, document_id)
        return False
    return True


async def sync_pending_payloads(
    session: AsyncSession, qdrant: AsyncQdrantClient, *, limit: int = 500
) -> int:
    """Outbox worker: retry the payload sync of `payload_dirty` documents, oldest first.
    Stops at the first failure (Qdrant is likely still down). Returns how many synced."""
    ids = list(
        await session.scalars(
            select(LegalDocument.id)
            .where(LegalDocument.payload_dirty)
            .order_by(LegalDocument.updated_at)
            .limit(limit)
        )
    )
    await session.commit()
    synced = 0
    for document_id in ids:
        if not await sync_document_payload(session, qdrant, document_id):
            break
        synced += 1
    if synced:
        logger.info("Synced the Qdrant payload of %d documents", synced)
    return synced


async def count_pending_payloads(session: AsyncSession) -> int:
    """Documents whose metadata is not yet in their Qdrant payload."""
    count = await session.scalar(
        select(func.count()).select_from(LegalDocument).where(LegalDocument.payload_dirty)
    )
    return count or 0


async def _reload(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Reload a document expired by a rollback, so callers never trigger an async lazy load."""
    try:
        await session.get(LegalDocument, document_id, populate_existing=True)
    except Exception:
        logger.exception("Could not reload document %s", document_id)


async def delete_document(
    session: AsyncSession, qdrant: AsyncQdrantClient, document_id: uuid.UUID
) -> None:
    """Delete the document's points, then the document (chunks cascade). Idempotent.

    An indexing run of the same document that is still embedding finds the row gone at
    its swap and writes nothing; one that is swapping finishes first (row lock).
    """
    async with session.begin_nested():
        await session.execute(
            select(LegalDocument.id).where(LegalDocument.id == document_id).with_for_update()
        )
        await _delete_points(qdrant, document_id)
        await session.execute(delete(LegalDocument).where(LegalDocument.id == document_id))
    await _commit(session)
