"""add dispute deadlines

ZR-ENG-CLR-010 Phase 10: deadline/SLA tracking (case_deadline), see
app/models/dispute_deadline.py.

Revision ID: 71d887d62a0c
Revises: 4bc9042c3d20
Create Date: 2026-09-15 00:00:07.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71d887d62a0c'
down_revision: Union[str, None] = '4bc9042c3d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispute_deadlines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=True),
        sa.Column("deadline_type", sa.String(length=20), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=15), nullable=False, server_default="PENDING"),
        sa.Column("extension_basis", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="ADMIN_SET"),
        sa.Column("created_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dispute_deadlines_case_id", "dispute_deadlines", ["case_id"])
    op.create_index("ix_dispute_deadlines_claim_id", "dispute_deadlines", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_deadlines_claim_id", table_name="dispute_deadlines")
    op.drop_index("ix_dispute_deadlines_case_id", table_name="dispute_deadlines")
    op.drop_table("dispute_deadlines")
