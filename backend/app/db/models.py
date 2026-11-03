"""SQLAlchemy ORM models. Schema reference: docs/02-data-model.md (Postgres)."""

import uuid
from datetime import date, datetime
from typing import Any, Literal, get_args

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Allowed values for text columns guarded by CHECK constraints.
USER_ROLES = ("user", "admin")
Provider = Literal["openai", "anthropic", "gemini"]
PROVIDERS: tuple[Provider, ...] = get_args(Provider)
# Display names for user-facing messages; must cover every Provider (checked by a test).
PROVIDER_LABELS: dict[Provider, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
}
DOCUMENT_SOURCES = ("vbpl", "user_upload")
DOCUMENT_STATUSES = (
    "con_hieu_luc",
    "het_hieu_luc_mot_phan",
    "het_hieu_luc_toan_bo",
    "chua_co_hieu_luc",
    "khong_xac_dinh",
)
DOCUMENT_SCOPES = ("trung_uong", "dia_phuong")
INGEST_STATUSES = ("pending", "processing", "done", "failed")
MESSAGE_ROLES = ("user", "assistant")
CRAWL_JOB_TYPES = ("backfill", "daily_new", "status_refresh")
CRAWL_RUN_STATUSES = ("running", "success", "failed", "partial")


def _in(column: str, values: tuple[str, ...]) -> str:
    """SQL text for a CHECK constraint `column IN (...)`."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    # Deterministic constraint names so Alembic migrations can drop / alter them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class TimestampMixin:
    # clock_timestamp(), not now(): rows inserted in one transaction keep their insert order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
        onupdate=func.clock_timestamp(),
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(_in("role", USER_ROLES), name="role"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, default="user", server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    settings: Mapped["UserSettings | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    api_keys: Mapped[list["UserApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class UserSettings(TimestampMixin, Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint(
            f"default_provider IS NULL OR {_in('default_provider', PROVIDERS)}",
            name="default_provider",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    default_provider: Mapped[str | None] = mapped_column(Text)
    default_model_id: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="settings")


class UserApiKey(TimestampMixin, Base):
    __tablename__ = "user_api_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "provider"),
        CheckConstraint(_in("provider", PROVIDERS), name="provider"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(Text)
    encrypted_key: Mapped[bytes] = mapped_column(LargeBinary)
    key_last4: Mapped[str] = mapped_column(Text)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="api_keys")

    def __repr__(self) -> str:
        # Never include the encrypted key.
        return f"UserApiKey(id={self.id!s}, provider={self.provider!r}, last4=****)"


class LegalDocument(TimestampMixin, Base):
    __tablename__ = "legal_documents"
    __table_args__ = (
        CheckConstraint(_in("source", DOCUMENT_SOURCES), name="source"),
        CheckConstraint(_in("status", DOCUMENT_STATUSES), name="status"),
        CheckConstraint(_in("scope", DOCUMENT_SCOPES), name="scope"),
        CheckConstraint(_in("ingest_status", INGEST_STATUSES), name="ingest_status"),
        CheckConstraint("source <> 'user_upload' OR owner_id IS NOT NULL", name="upload_has_owner"),
        Index(
            "uq_legal_documents_source_source_id",
            "source",
            "source_id",
            unique=True,
            postgresql_where=text("source_id IS NOT NULL"),
        ),
        Index(
            "ix_legal_documents_payload_dirty",
            "payload_dirty",
            postgresql_where=text("payload_dirty"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str | None] = mapped_column(Text)
    # No ON DELETE CASCADE: deleting uploads must go through the indexer (Qdrant first).
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    so_hieu: Mapped[str | None] = mapped_column(Text)
    so_hieu_norm: Mapped[str | None] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str | None] = mapped_column(Text)
    issuing_body: Mapped[str | None] = mapped_column(Text)
    issued_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        Text, default="khong_xac_dinh", server_default="khong_xac_dinh", index=True
    )
    status_note: Mapped[str | None] = mapped_column(Text)
    replaced_by: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'")
    )
    amended_by: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'")
    )
    is_consolidated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    scope: Mapped[str] = mapped_column(Text, default="trung_uong", server_default="trung_uong")
    source_url: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str | None] = mapped_column(Text)
    # NULL until the text is parsed (uploads are created as `pending` before parsing).
    content_hash: Mapped[str | None] = mapped_column(Text)
    ingest_status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    ingest_error: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    # Metadata committed but not yet copied to the Qdrant payload (outbox, see indexer).
    payload_dirty: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    chunks: Mapped[list["LegalChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="LegalChunk.ordinal",
    )


class LegalChunk(TimestampMixin, Base):
    __tablename__ = "legal_chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)

    # Same value as the Qdrant point id.
    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("legal_documents.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    phan: Mapped[str | None] = mapped_column(Text)
    chuong: Mapped[str | None] = mapped_column(Text)
    muc: Mapped[str | None] = mapped_column(Text)
    dieu: Mapped[str | None] = mapped_column(Text)
    dieu_title: Mapped[str | None] = mapped_column(Text)
    khoan: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    embed_text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)

    document: Mapped[LegalDocument] = relationship(back_populates="chunks")


class Conversation(TimestampMixin, Base):
    # `updated_at` is also bumped by the DB trigger `messages_touch_conversation` on each message.
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_id_updated_at", "user_id", "updated_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(_in("role", MESSAGE_ROLES), name="role"),
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    retrieved_chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, server_default=text("'{}'")
    )
    refused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class CrawlRun(TimestampMixin, Base):
    __tablename__ = "crawl_runs"
    __table_args__ = (
        CheckConstraint(_in("job_type", CRAWL_JOB_TYPES), name="job_type"),
        CheckConstraint(_in("status", CRAWL_RUN_STATUSES), name="status"),
        Index("ix_crawl_runs_job_type_started_at", "job_type", "started_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_type: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="running", server_default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
