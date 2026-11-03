"""legal_documents.payload_dirty: metadata not yet copied to the Qdrant payload

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "legal_documents",
        sa.Column("payload_dirty", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index(
        "ix_legal_documents_payload_dirty",
        "legal_documents",
        ["payload_dirty"],
        unique=False,
        postgresql_where=sa.text("payload_dirty"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_legal_documents_payload_dirty",
        table_name="legal_documents",
        postgresql_where=sa.text("payload_dirty"),
    )
    op.drop_column("legal_documents", "payload_dirty")
