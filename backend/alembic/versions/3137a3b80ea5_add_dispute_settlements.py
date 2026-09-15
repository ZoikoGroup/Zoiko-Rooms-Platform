"""add dispute settlements

ZR-ENG-CLR-010 Phase 4: bilateral settlement negotiation for A1/A2 claims,
see app/models/dispute_settlement.py.

Revision ID: 3137a3b80ea5
Revises: 33103f957b8e
Create Date: 2026-09-15 00:00:04.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3137a3b80ea5'
down_revision: Union[str, None] = '33103f957b8e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispute_settlements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposed_by_role", sa.String(length=10), nullable=False),
        sa.Column("proposed_by_guest_id", sa.String(), sa.ForeignKey("guests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("proposed_by_party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=15), nullable=False, server_default="SENT"),
        sa.Column("terms_text", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("terms_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("acknowledges_no_nonwaivable_waiver", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("offered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_by_guest_id", sa.String(), sa.ForeignKey("guests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("responded_by_party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_note", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_settlement_id", sa.Integer(), sa.ForeignKey("dispute_settlements.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dispute_settlements_case_id", "dispute_settlements", ["case_id"])

    op.create_table(
        "dispute_settlement_claim_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("settlement_id", sa.Integer(), sa.ForeignKey("dispute_settlements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("settlement_id", "claim_id", name="uq_dispute_settlement_claim_links_settlement_claim"),
    )
    op.create_index("ix_dispute_settlement_claim_links_settlement_id", "dispute_settlement_claim_links", ["settlement_id"])
    op.create_index("ix_dispute_settlement_claim_links_claim_id", "dispute_settlement_claim_links", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_settlement_claim_links_claim_id", table_name="dispute_settlement_claim_links")
    op.drop_index("ix_dispute_settlement_claim_links_settlement_id", table_name="dispute_settlement_claim_links")
    op.drop_table("dispute_settlement_claim_links")
    op.drop_index("ix_dispute_settlements_case_id", table_name="dispute_settlements")
    op.drop_table("dispute_settlements")
