"""Documents business logic: upload + background ingest, list, detail, delete.

Access rule (same as the retriever, CLAUDE.md rule 5): a user sees every `vbpl` document
and only their own uploads. Writes to Qdrant go through `app.ingest.indexer`.
"""

import asyncio
import io
import logging
import uuid
import zipfile
from pathlib import Path

import anyio
from fastapi import UploadFile, status
from qdrant_client import AsyncQdrantClient
from sqlalchemy import ColumnElement, Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.db.models import LegalChunk, LegalDocument, User
from app.db.session import SessionFactory
from app.documents.schemas import (
    DieuOut,
    DocumentDetail,
    DocumentList,
    DocumentSummary,
    Scope,
    UploadMetadata,
)
from app.ingest import indexer
from app.ingest.parsers import SUPPORTED_EXTENSIONS, ParsedDocument, ParseError, parse_bytes
from app.ingest.parsers.base import USER_MESSAGES
from app.ingest.parsers.txt import looks_like_text
from app.ingest.schemas import Status, normalize_so_hieu

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Signatures that are never plain text, checked for `.txt` uploads.
_BINARY_SIGNATURES = (b"%PDF-", b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"\x89PNG", b"\xff\xd8\xff")
INGEST_INTERRUPTED_MESSAGE = "Không đọc được file đã tải lên. Vui lòng tải lên lại."

logger = logging.getLogger(__name__)


def _not_found() -> AppError:
    return AppError(status.HTTP_404_NOT_FOUND, "not_found", "Không tìm thấy văn bản.")


# --- Upload validation -------------------------------------------------------------------


def matches_format(data: bytes, ext: str) -> bool:
    """Check the content against the extension (magic bytes), not only the file name."""
    if ext == ".pdf":
        return b"%PDF-" in data[:1024]  # the spec tolerates leading junk before the header
    if ext == ".docx":
        if not data.startswith(b"PK\x03\x04"):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                return "word/document.xml" in archive.namelist()
        except zipfile.BadZipFile:
            return False
    if ext == ".txt":
        return not data.startswith(_BINARY_SIGNATURES) and looks_like_text(data)
    return False


async def read_upload(file: UploadFile) -> tuple[bytes, str]:
    """Return the content and lower-case extension of a valid upload, else raise `AppError`."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise AppError(
            status.HTTP_400_BAD_REQUEST,
            "unsupported_format",
            USER_MESSAGES["unsupported_format"],
        )
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError(
            status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large", "File vượt quá giới hạn 20 MB."
        )
    if not data:
        raise AppError(status.HTTP_400_BAD_REQUEST, "empty_file", "File rỗng.")
    if not matches_format(data, ext):
        raise AppError(
            status.HTTP_400_BAD_REQUEST,
            "invalid_file",
            f"Nội dung file không khớp với định dạng {ext.lstrip('.').upper()}.",
        )
    return data, ext


async def parse_upload(data: bytes, ext: str) -> ParsedDocument:
    try:
        return await asyncio.to_thread(parse_bytes, data, f"upload{ext}")
    except ParseError as exc:
        raise AppError(status.HTTP_400_BAD_REQUEST, exc.code, exc.user_message) from None


# --- Upload storage + ingest -------------------------------------------------------------


def upload_path(owner_id: uuid.UUID, document_id: uuid.UUID, ext: str) -> Path:
    return settings.upload_dir / str(owner_id) / f"{document_id}{ext}"


def find_upload_file(owner_id: uuid.UUID, document_id: uuid.UUID) -> Path | None:
    for ext in sorted(SUPPORTED_EXTENSIONS):
        path = upload_path(owner_id, document_id, ext)
        if path.is_file():
            return path
    return None


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.part")
    tmp.write_bytes(data)
    tmp.replace(path)


async def create_upload(
    session: AsyncSession, user: User, metadata: UploadMetadata, data: bytes, ext: str
) -> tuple[LegalDocument, Path]:
    """Store the original file and create the document as `pending`."""
    fields = metadata.model_dump()
    doc = LegalDocument(
        id=uuid.uuid4(),
        source="user_upload",
        owner_id=user.id,
        so_hieu_norm=normalize_so_hieu(fields["so_hieu"]),
        ingest_status="pending",
        **fields,
    )
    path = upload_path(user.id, doc.id, ext)
    await asyncio.to_thread(_write_file, path, data)
    session.add(doc)
    try:
        await session.commit()
    except BaseException:
        await session.rollback()
        path.unlink(missing_ok=True)
        raise
    logger.info("User %s uploaded document %s (%d bytes)", user.id, doc.id, len(data))
    return doc, path


async def _set_ingest_status(
    session: AsyncSession, document_id: uuid.UUID, ingest_status: str, message: str | None
) -> None:
    await session.execute(
        update(LegalDocument)
        .where(LegalDocument.id == document_id)
        .values(
            ingest_status=ingest_status,
            ingest_error=message,
            updated_at=func.clock_timestamp(),  # Core UPDATE skips the ORM `onupdate`
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def _mark_failed(session: AsyncSession, document_id: uuid.UUID, message: str) -> None:
    await _set_ingest_status(session, document_id, "failed", message)


async def ingest_upload(
    session_factory: SessionFactory,
    qdrant: AsyncQdrantClient,
    document_id: uuid.UUID,
    path: Path,
) -> None:
    """Background task: parse the stored file and index it. Never raises; failures end up
    in `ingest_status=failed` + `ingest_error` (Vietnamese, shown to the user)."""
    async with session_factory() as session:
        try:
            try:
                data = await asyncio.to_thread(path.read_bytes)
                parsed = await asyncio.to_thread(parse_bytes, data, path.name)
            except FileNotFoundError:
                # Deleted meanwhile (the delete removes the file); else the file is lost.
                if await session.get(LegalDocument, document_id) is not None:
                    await _mark_failed(session, document_id, INGEST_INTERRUPTED_MESSAGE)
                return
            except ParseError as exc:
                await _mark_failed(session, document_id, exc.user_message)
                return
            await indexer.reindex_document(session, qdrant, document_id, parsed.text)
        except asyncio.CancelledError:
            # Shutdown / reload: the indexer marked it failed; put it back to `pending` so
            # `resume_interrupted_uploads` picks it up at the next start.
            with anyio.move_on_after(indexer.CLEANUP_TIMEOUT_S, shield=True):
                try:
                    await session.rollback()
                    await _set_ingest_status(session, document_id, "pending", None)
                except Exception:
                    logger.exception("Could not reset interrupted document %s", document_id)
            raise
        except LookupError:
            logger.info("Document %s was deleted before its ingest finished", document_id)
        except Exception:
            # The indexer already marked it failed when it got that far.
            logger.exception("Ingest of uploaded document %s failed", document_id)
            try:
                await session.rollback()
                doc = await session.get(LegalDocument, document_id, populate_existing=True)
                if doc is not None and doc.ingest_status != "failed":
                    await _mark_failed(session, document_id, indexer.INDEX_FAILED_MESSAGE)
            except Exception:
                logger.exception("Could not mark document %s as failed", document_id)


async def resume_interrupted_uploads(
    session_factory: SessionFactory, qdrant: AsyncQdrantClient
) -> int:
    """At startup: ingest uploads left `pending` / `processing` by a stopped process
    (background tasks do not survive a restart). Returns how many were resumed."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(LegalDocument.id, LegalDocument.owner_id)
                .where(
                    LegalDocument.source == "user_upload",
                    LegalDocument.ingest_status.in_(("pending", "processing")),
                )
                .order_by(LegalDocument.created_at)
            )
        ).all()
    if rows:
        # Qdrant down (e.g. started together with the backend): leave them for the next
        # start instead of marking every one failed. Raises; the caller logs it.
        await qdrant.get_collections()
    for document_id, owner_id in rows:
        path = find_upload_file(owner_id, document_id)
        if path is None:
            async with session_factory() as session:
                await _mark_failed(session, document_id, INGEST_INTERRUPTED_MESSAGE)
            continue
        await ingest_upload(session_factory, qdrant, document_id, path)
    if rows:
        logger.info("Resumed the ingest of %d interrupted uploads", len(rows))
    return len(rows)


# --- Read --------------------------------------------------------------------------------


def visible_to(user: User) -> ColumnElement[bool]:
    return or_(LegalDocument.source == "vbpl", LegalDocument.owner_id == user.id)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _list_query(
    user: User, scope: Scope, q: str | None, doc_type: str | None, doc_status: Status | None
) -> Select[tuple[LegalDocument]]:
    query = select(LegalDocument).where(visible_to(user))
    if scope == "mine":
        query = query.where(LegalDocument.owner_id == user.id)
    elif scope == "vbpl":
        query = query.where(LegalDocument.source == "vbpl")
    if q:
        pattern = func.unaccent(f"%{_escape_like(q)}%")
        query = query.where(
            or_(
                func.unaccent(LegalDocument.title).ilike(pattern, escape="\\"),
                func.unaccent(LegalDocument.so_hieu).ilike(pattern, escape="\\"),
            )
        )
    if doc_type:
        query = query.where(LegalDocument.doc_type == doc_type)
    if doc_status:
        query = query.where(LegalDocument.status == doc_status)
    return query


async def list_documents(
    session: AsyncSession,
    user: User,
    *,
    scope: Scope,
    q: str | None,
    doc_type: str | None,
    doc_status: Status | None,
    page: int,
    page_size: int,
) -> DocumentList:
    query = _list_query(user, scope, q, doc_type, doc_status)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(
        query.order_by(
            LegalDocument.issued_date.desc().nulls_last(),
            LegalDocument.created_at.desc(),
            LegalDocument.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return DocumentList(
        items=[DocumentSummary.model_validate(row) for row in rows], total=total or 0
    )


def can_delete(doc: LegalDocument, user: User) -> bool:
    return user.role == "admin" or (doc.source == "user_upload" and doc.owner_id == user.id)


async def get_document(session: AsyncSession, user: User, document_id: uuid.UUID) -> DocumentDetail:
    doc = await session.scalar(
        select(LegalDocument).where(LegalDocument.id == document_id, visible_to(user))
    )
    if doc is None:
        raise _not_found()
    chunks = (
        await session.execute(
            select(LegalChunk.dieu, LegalChunk.dieu_title)
            .where(LegalChunk.document_id == document_id)
            .order_by(LegalChunk.ordinal)
        )
    ).all()
    # A long Điều spans several chunks; list it once, with the first title found.
    titles: dict[str, str | None] = {}
    for dieu, dieu_title in chunks:
        if dieu is not None and titles.get(dieu) is None:
            titles[dieu] = dieu_title
    columns = DocumentDetail.model_fields.keys() & LegalDocument.__table__.columns.keys()
    fields = {name: getattr(doc, name) for name in columns}
    return DocumentDetail(
        **fields,
        chunk_count=len(chunks),
        dieu_list=[DieuOut(dieu=dieu, dieu_title=title) for dieu, title in titles.items()],
        can_delete=can_delete(doc, user),
    )


# --- Delete ------------------------------------------------------------------------------


async def delete_document(
    session: AsyncSession, qdrant: AsyncQdrantClient, user: User, document_id: uuid.UUID
) -> None:
    """Owners delete their uploads; admins delete any document. Another user's upload
    reads as missing (404), a visible document one may not delete is 403."""
    doc = await session.get(LegalDocument, document_id)
    if doc is None or (user.role != "admin" and doc.source != "vbpl" and doc.owner_id != user.id):
        raise _not_found()
    if not can_delete(doc, user):
        raise AppError(
            status.HTTP_403_FORBIDDEN, "forbidden", "Bạn không có quyền xoá văn bản này."
        )
    owner_id = doc.owner_id
    try:
        await indexer.delete_document(session, qdrant, document_id)
    except Exception as exc:
        logger.warning("Delete of document %s failed: %s", document_id, type(exc).__name__)
        raise AppError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "service_unavailable",
            "Không xoá được văn bản lúc này. Vui lòng thử lại sau.",
        ) from None
    logger.info("User %s deleted document %s", user.id, document_id)
    if owner_id is not None:
        path = find_upload_file(owner_id, document_id)
        try:
            if path is not None:
                await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            logger.exception("Could not remove the file of deleted document %s", document_id)
