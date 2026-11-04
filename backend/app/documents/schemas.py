"""Documents API request/response schemas (docs/06-api.md, "Documents")."""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.ingest.schemas import STATUS_LABELS, Source, Status

Scope = Literal["all", "mine", "vbpl"]
IngestStatus = Literal["pending", "processing", "done", "failed"]

TITLE_MAX = 1000
SHORT_FIELD_MAX = 200
PAGE_SIZE_MAX = 50


class UploadMetadata(BaseModel):
    """Form fields of `POST /documents/upload`. Empty strings (blank inputs) mean "not set"."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX)
    so_hieu: str | None = Field(default=None, max_length=SHORT_FIELD_MAX)
    doc_type: str | None = Field(default=None, max_length=SHORT_FIELD_MAX)
    issued_date: date | None = None
    effective_date: date | None = None
    status: Status = "khong_xac_dinh"

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = " ".join(value.split())
            return value or None
        return value


class PreviewOut(BaseModel):
    detected: dict[str, Any]
    warnings: list[dict[str, Any]]


class UploadAccepted(BaseModel):
    id: uuid.UUID
    ingest_status: IngestStatus


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: Source
    so_hieu: str | None
    title: str
    doc_type: str | None
    issued_date: date | None
    effective_date: date | None
    status: Status
    ingest_status: IngestStatus
    source_url: str | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    total: int


class DieuOut(BaseModel):
    dieu: str
    dieu_title: str | None


class DocumentDetail(DocumentSummary):
    issuing_body: str | None
    expiry_date: date | None
    status_note: str | None
    replaced_by: list[str]
    amended_by: list[str]
    is_consolidated: bool
    file_url: str | None
    ingest_error: str | None
    updated_at: datetime
    chunk_count: int
    dieu_list: list[DieuOut]
    can_delete: bool
