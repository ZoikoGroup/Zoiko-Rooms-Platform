"""add evidence vault artifacts

Revision ID: a86208d4c278
Revises: a30271e85428
Create Date: 2026-09-15 11:09:17.862140

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a86208d4c278'
down_revision: Union[str, None] = 'a30271e85428'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "evidence_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("related_entity_type", sa.String(length=50), nullable=False),
        sa.Column("related_entity_id", sa.String(length=50), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("scan_status", sa.String(length=20), nullable=False, server_default="NOT_SCANNED"),
        sa.Column("uploaded_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_artifacts_sha256_hash", "evidence_artifacts", ["sha256_hash"])
    op.create_index("ix_evidence_artifacts_related_entity", "evidence_artifacts", ["related_entity_type", "related_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_artifacts_related_entity", table_name="evidence_artifacts")
    op.drop_index("ix_evidence_artifacts_sha256_hash", table_name="evidence_artifacts")
    op.drop_table("evidence_artifacts")
