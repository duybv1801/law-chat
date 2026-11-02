"""initial schema: extensions, all tables, indexes and CHECK constraints (docs/02-data-model.md)

Revision ID: 0001
Revises:
Create Date: 2026-11-02 11:30:03.275291

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("citext", "unaccent")

TOUCH_CONVERSATION_FUNCTION = """
CREATE FUNCTION touch_conversation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE conversations SET updated_at = NEW.created_at WHERE id = NEW.conversation_id;
    RETURN NEW;
END
$$
"""


def upgrade() -> None:
    """Upgrade schema."""
    for extension in EXTENSIONS:
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="running", nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "job_type IN ('backfill', 'daily_new', 'status_refresh')",
            name=op.f("ck_crawl_runs_job_type"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'partial')",
            name=op.f("ck_crawl_runs_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_runs")),
    )
    op.create_index(
        "ix_crawl_runs_job_type_started_at", "crawl_runs", ["job_type", "started_at"], unique=False
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default="user", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'admin')", name=op.f("ck_users_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_conversations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index(
        "ix_conversations_user_id_updated_at",
        "conversations",
        ["user_id", "updated_at"],
        unique=False,
    )
    op.create_table(
        "legal_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.UUID(), nullable=True),
        sa.Column("so_hieu", sa.Text(), nullable=True),
        sa.Column("so_hieu_norm", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=True),
        sa.Column("issuing_body", sa.Text(), nullable=True),
        sa.Column("issued_date", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), server_default="khong_xac_dinh", nullable=False),
        sa.Column("status_note", sa.Text(), nullable=True),
        sa.Column(
            "replaced_by", sa.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column(
            "amended_by", sa.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("is_consolidated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("scope", sa.Text(), server_default="trung_uong", nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("ingest_status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("ingest_error", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ingest_status IN ('pending', 'processing', 'done', 'failed')",
            name=op.f("ck_legal_documents_ingest_status"),
        ),
        sa.CheckConstraint(
            "scope IN ('trung_uong', 'dia_phuong')", name=op.f("ck_legal_documents_scope")
        ),
        sa.CheckConstraint(
            "source <> 'user_upload' OR owner_id IS NOT NULL",
            name=op.f("ck_legal_documents_upload_has_owner"),
        ),
        sa.CheckConstraint(
            "source IN ('vbpl', 'user_upload')", name=op.f("ck_legal_documents_source")
        ),
        sa.CheckConstraint(
            "status IN ('con_hieu_luc', 'het_hieu_luc_mot_phan', 'het_hieu_luc_toan_bo', "
            "'chua_co_hieu_luc', 'khong_xac_dinh')",
            name=op.f("ck_legal_documents_status"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_legal_documents_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_documents")),
    )
    op.create_index(
        op.f("ix_legal_documents_owner_id"), "legal_documents", ["owner_id"], unique=False
    )
    op.create_index(
        op.f("ix_legal_documents_so_hieu_norm"), "legal_documents", ["so_hieu_norm"], unique=False
    )
    op.create_index(op.f("ix_legal_documents_status"), "legal_documents", ["status"], unique=False)
    op.create_index(
        op.f("ix_legal_documents_status_checked_at"),
        "legal_documents",
        ["status_checked_at"],
        unique=False,
    )
    op.create_index(
        "uq_legal_documents_source_source_id",
        "legal_documents",
        ["source", "source_id"],
        unique=True,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=False),
        sa.Column("key_last4", sa.Text(), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('openai', 'anthropic', 'gemini')", name=op.f("ck_user_api_keys_provider")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_api_keys_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_api_keys")),
        sa.UniqueConstraint("user_id", "provider", name=op.f("uq_user_api_keys_user_id_provider")),
    )
    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("default_provider", sa.Text(), nullable=True),
        sa.Column("default_model_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "default_provider IS NULL OR default_provider IN ('openai', 'anthropic', 'gemini')",
            name=op.f("ck_user_settings_default_provider"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_settings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_settings")),
    )
    op.create_table(
        "legal_chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("phan", sa.Text(), nullable=True),
        sa.Column("chuong", sa.Text(), nullable=True),
        sa.Column("muc", sa.Text(), nullable=True),
        sa.Column("dieu", sa.Text(), nullable=True),
        sa.Column("dieu_title", sa.Text(), nullable=True),
        sa.Column("khoan", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embed_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["legal_documents.id"],
            name=op.f("fk_legal_chunks_document_id_legal_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_legal_chunks")),
        sa.UniqueConstraint(
            "document_id", "ordinal", name=op.f("uq_legal_chunks_document_id_ordinal")
        ),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "retrieved_chunk_ids",
            sa.ARRAY(sa.UUID()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("refused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("usage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_messages_role")),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        "ix_messages_conversation_id_created_at",
        "messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    # A new message marks its conversation as recently active (conversation list order).
    op.execute(TOUCH_CONVERSATION_FUNCTION)
    op.execute(
        "CREATE TRIGGER messages_touch_conversation AFTER INSERT ON messages "
        "FOR EACH ROW EXECUTE FUNCTION touch_conversation()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS messages_touch_conversation ON messages")
    op.execute("DROP FUNCTION IF EXISTS touch_conversation()")
    op.drop_index("ix_messages_conversation_id_created_at", table_name="messages")
    op.drop_table("messages")
    op.drop_table("legal_chunks")
    op.drop_table("user_settings")
    op.drop_table("user_api_keys")
    op.drop_index(
        "uq_legal_documents_source_source_id",
        table_name="legal_documents",
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_legal_documents_status_checked_at"), table_name="legal_documents")
    op.drop_index(op.f("ix_legal_documents_status"), table_name="legal_documents")
    op.drop_index(op.f("ix_legal_documents_so_hieu_norm"), table_name="legal_documents")
    op.drop_index(op.f("ix_legal_documents_owner_id"), table_name="legal_documents")
    op.drop_table("legal_documents")
    op.drop_index("ix_conversations_user_id_updated_at", table_name="conversations")
    op.drop_table("conversations")
    op.drop_table("users")
    op.drop_index("ix_crawl_runs_job_type_started_at", table_name="crawl_runs")
    op.drop_table("crawl_runs")
    # Extensions are kept: the Docker init SQL creates them too, and other objects may use them.
