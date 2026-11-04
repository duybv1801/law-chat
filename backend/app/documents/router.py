"""Upload, list, detail and delete document routes (docs/06-api.md, "Documents")."""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.deps import CurrentUser, QdrantDep, SessionDep, SessionFactoryDep
from app.documents import service
from app.documents.schemas import (
    PAGE_SIZE_MAX,
    DocumentDetail,
    DocumentList,
    PreviewOut,
    Scope,
    UploadAccepted,
    UploadMetadata,
)
from app.ingest.schemas import Status

router = APIRouter(prefix="/documents", tags=["documents"])

FileDep = Annotated[UploadFile, File()]
FormStr = Annotated[str | None, Form()]


@router.post("/upload/preview")
async def upload_preview(_user: CurrentUser, file: FileDep) -> PreviewOut:
    """Parse only, to pre-fill the upload form. Nothing is stored."""
    data, ext = await service.read_upload(file)
    parsed = await service.parse_upload(data, ext)
    return PreviewOut(detected=parsed.detected, warnings=parsed.warnings)


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload(
    user: CurrentUser,
    session: SessionDep,
    session_factory: SessionFactoryDep,
    qdrant: QdrantDep,
    background: BackgroundTasks,
    file: FileDep,
    title: FormStr = None,
    so_hieu: FormStr = None,
    doc_type: FormStr = None,
    issued_date: FormStr = None,
    effective_date: FormStr = None,
    doc_status: Annotated[str | None, Form(alias="status")] = None,
) -> UploadAccepted:
    fields = {
        "title": title,
        "so_hieu": so_hieu,
        "doc_type": doc_type,
        "issued_date": issued_date,
        "effective_date": effective_date,
        "status": doc_status,
    }
    try:
        # Blank (empty or whitespace-only) inputs mean "not set" (status falls back to its default).
        metadata = UploadMetadata.model_validate(
            {k: v for k, v in fields.items() if k == "title" or (v and v.strip())}
        )
    except ValidationError as exc:
        raise RequestValidationError(
            [{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]
        ) from None
    data, ext = await service.read_upload(file)
    doc, path = await service.create_upload(session, user, metadata, data, ext)
    background.add_task(service.ingest_upload, session_factory, qdrant, doc.id, path)
    return UploadAccepted(id=doc.id, ingest_status=doc.ingest_status)


@router.get("")
async def list_documents(
    user: CurrentUser,
    session: SessionDep,
    scope: Scope = "all",
    q: Annotated[str | None, Query(max_length=200)] = None,
    doc_type: Annotated[str | None, Query(max_length=200)] = None,
    doc_status: Annotated[Status | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = 20,
) -> DocumentList:
    return await service.list_documents(
        session,
        user,
        scope=scope,
        q=q.strip() if q else None,
        doc_type=doc_type,
        doc_status=doc_status,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}")
async def get_document(
    document_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> DocumentDetail:
    return await service.get_document(session, user, document_id)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, user: CurrentUser, session: SessionDep, qdrant: QdrantDep
) -> None:
    await service.delete_document(session, qdrant, user, document_id)
