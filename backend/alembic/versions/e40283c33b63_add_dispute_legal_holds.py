"""add dispute_legal_holds

ZR-ENG-CLR-010 Phase 20: Section 21 legal hold as a first-class,
auditable object, see app/models/dispute_legal_hold.py.

Revision ID: e40283c33b63
Revises: 718373cba56a
Create Date: 2026-09-15 00:00:16.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e40283c33b63'
down_revision: Union[str, None] = '718373cba56a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispute_legal_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("dispute_evidence_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="ACTIVE"),
        sa.Column("reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("placed_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dispute_legal_holds_evidence_id", "dispute_legal_holds", ["evidence_id"])
    op.create_index("ix_dispute_legal_holds_case_id", "dispute_legal_holds", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_legal_holds_case_id", table_name="dispute_legal_holds")
    op.drop_index("ix_dispute_legal_holds_evidence_id", table_name="dispute_legal_holds")
    op.drop_table("dispute_legal_holds")
