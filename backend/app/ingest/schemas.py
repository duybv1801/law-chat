"""Input schema of the indexer: document metadata from the crawler or the upload form."""

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["vbpl", "user_upload"]
Status = Literal[
    "con_hieu_luc",
    "het_hieu_luc_mot_phan",
    "het_hieu_luc_toan_bo",
    "chua_co_hieu_luc",
    "khong_xac_dinh",
]
Scope = Literal["trung_uong", "dia_phuong"]

_SPACES = re.compile(r"\s+")


def normalize_so_hieu(so_hieu: str | None) -> str | None:
    """`so_hieu_norm`: no whitespace, upper case ("45/2019/qh 14" -> "45/2019/QH14")."""
    if so_hieu is None:
        return None
    return _SPACES.sub("", so_hieu).upper() or None


class DocumentMetadata(BaseModel):
    """Metadata columns of `legal_documents` that callers may set or change.

    Excludes identity (`source`, `source_id`, `owner_id`), derived columns (`so_hieu_norm`)
    and ingestion state (`content_hash`, `ingest_*`, `embedding_model`).
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    so_hieu: str | None = None
    doc_type: str | None = None
    issuing_body: str | None = None
    issued_date: date | None = None
    effective_date: date | None = None
    expiry_date: date | None = None
    status: Status = "khong_xac_dinh"
    status_note: str | None = None
    replaced_by: list[str] = Field(default_factory=list)
    amended_by: list[str] = Field(default_factory=list)
    is_consolidated: bool = False
    scope: Scope = "trung_uong"
    source_url: str | None = None
    file_url: str | None = None
    crawled_at: datetime | None = None
    status_checked_at: datetime | None = None


class LegalDocumentIn(DocumentMetadata):
    source: Source
    source_id: str | None = None  # vbpl.vn id; None for uploads
