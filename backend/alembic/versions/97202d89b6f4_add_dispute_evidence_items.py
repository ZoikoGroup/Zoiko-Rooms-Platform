"""add dispute evidence items and claim links

ZR-ENG-CLR-010 Phase 2: evidence originals (private disk storage + SHA-256
hash + provenance + disclosure class + legal hold), see
app/models/dispute_evidence.py.

Revision ID: 97202d89b6f4
Revises: c439f07d79ac
Create Date: 2026-09-15 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '97202d89b6f4'
down_revision: Union[str, None] = 'c439f07d79ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispute_evidence_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provenance", sa.String(length=20), nullable=False),
        sa.Column("uploaded_by_guest_id", sa.String(), sa.ForeignKey("guests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by_party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("stored_filename", sa.String(length=255), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("note_text", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("disclosure_class", sa.String(length=30), nullable=False, server_default="PARTY_VISIBLE"),
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("redacted_of_evidence_id", sa.Integer(), sa.ForeignKey("dispute_evidence_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dispute_evidence_items_case_id", "dispute_evidence_items", ["case_id"])

    op.create_table(
        "dispute_evidence_claim_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("dispute_evidence_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("evidence_id", "claim_id", name="uq_dispute_evidence_claim_links_evidence_claim"),
    )
    op.create_index("ix_dispute_evidence_claim_links_evidence_id", "dispute_evidence_claim_links", ["evidence_id"])
    op.create_index("ix_dispute_evidence_claim_links_claim_id", "dispute_evidence_claim_links", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_evidence_claim_links_claim_id", table_name="dispute_evidence_claim_links")
    op.drop_index("ix_dispute_evidence_claim_links_evidence_id", table_name="dispute_evidence_claim_links")
    op.drop_table("dispute_evidence_claim_links")
    op.drop_index("ix_dispute_evidence_items_case_id", table_name="dispute_evidence_items")
    op.drop_table("dispute_evidence_items")
