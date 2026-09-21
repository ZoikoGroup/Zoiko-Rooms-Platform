"""add agreement legal holds table

Section 4 gap: agreements had no retention/legal-hold model at all, see
app/models/agreement_legal_hold.py.

Revision ID: 37d8aabc0798
Revises: c7c4d17df125
Create Date: 2026-09-19 12:28:55.751498

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '37d8aabc0798'
down_revision: Union[str, None] = 'c7c4d17df125'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agreement_legal_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agreement_id", sa.Integer(), sa.ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="ACTIVE"),
        sa.Column("reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("authority_evidence_ref", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("placed_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agreement_legal_holds_agreement_id", "agreement_legal_holds", ["agreement_id"])


def downgrade() -> None:
    op.drop_index("ix_agreement_legal_holds_agreement_id", table_name="agreement_legal_holds")
    op.drop_table("agreement_legal_holds")
