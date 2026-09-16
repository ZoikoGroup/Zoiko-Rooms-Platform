"""add dispute external proceedings

ZR-ENG-CLR-010 Phase 3: external proceeding filing/decision tracking for
A2+ (external-only) claims, see app/models/dispute_external_proceeding.py.

Revision ID: 33103f957b8e
Revises: 97202d89b6f4
Create Date: 2026-09-15 00:00:03.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '33103f957b8e'
down_revision: Union[str, None] = '97202d89b6f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispute_external_proceedings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("authority_type", sa.String(length=30), nullable=False),
        sa.Column("authority_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("external_reference", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=15), nullable=False, server_default="FILED"),
        sa.Column("finality_state", sa.String(length=15), nullable=True),
        sa.Column("filed_at", sa.Date(), nullable=True),
        sa.Column("decision_date", sa.Date(), nullable=True),
        sa.Column("outcome_evidence_id", sa.Integer(), sa.ForeignKey("dispute_evidence_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("outcome_summary", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("filed_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("decided_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dispute_external_proceedings_case_id", "dispute_external_proceedings", ["case_id"])

    op.create_table(
        "dispute_external_proceeding_claim_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proceeding_id", sa.Integer(), sa.ForeignKey("dispute_external_proceedings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("proceeding_id", "claim_id", name="uq_dispute_ext_proceeding_claim_links_proceeding_claim"),
    )
    op.create_index("ix_dispute_external_proceeding_claim_links_proceeding_id", "dispute_external_proceeding_claim_links", ["proceeding_id"])
    op.create_index("ix_dispute_external_proceeding_claim_links_claim_id", "dispute_external_proceeding_claim_links", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_external_proceeding_claim_links_claim_id", table_name="dispute_external_proceeding_claim_links")
    op.drop_index("ix_dispute_external_proceeding_claim_links_proceeding_id", table_name="dispute_external_proceeding_claim_links")
    op.drop_table("dispute_external_proceeding_claim_links")
    op.drop_index("ix_dispute_external_proceedings_case_id", table_name="dispute_external_proceedings")
    op.drop_table("dispute_external_proceedings")
