"""add dispute_decisions

ZR-ENG-CLR-010 Phase 19: Section 19/22/26 append-only claim-decision
history, see app/models/dispute_decision.py.

Revision ID: 718373cba56a
Revises: 9cf005193d5b
Create Date: 2026-09-15 00:00:15.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '718373cba56a'
down_revision: Union[str, None] = '9cf005193d5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispute_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("decision_basis", sa.String(length=30), nullable=False),
        sa.Column("authority", sa.String(length=20), nullable=False),
        sa.Column("decided_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("reason_code", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("external_proceeding_id", sa.Integer(), sa.ForeignKey("dispute_external_proceedings.id", ondelete="SET NULL"), nullable=True),
        sa.Column("settlement_id", sa.Integer(), sa.ForeignKey("dispute_settlements.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dispute_decisions_claim_id", "dispute_decisions", ["claim_id"])
    op.create_index("ix_dispute_decisions_case_id", "dispute_decisions", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_decisions_case_id", table_name="dispute_decisions")
    op.drop_index("ix_dispute_decisions_claim_id", table_name="dispute_decisions")
    op.drop_table("dispute_decisions")
