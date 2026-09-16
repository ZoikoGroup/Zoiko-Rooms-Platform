"""add dispute parties

ZR-ENG-CLR-010 Phase 15: dispute_party representation/authority tracking,
see app/models/dispute_party.py.

Revision ID: ea18de309776
Revises: b6833bf3ba62
Create Date: 2026-09-15 00:00:12.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea18de309776'
down_revision: Union[str, None] = 'b6833bf3ba62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispute_parties",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("party_role", sa.String(length=15), nullable=False),
        sa.Column("guest_id", sa.String(), sa.ForeignKey("guests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("represents", sa.String(length=10), nullable=True),
        sa.Column("representation_type", sa.String(length=20), nullable=False, server_default="SELF"),
        sa.Column("authority_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authority_evidence_ref", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("communication_restrictions", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("added_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
    )
    op.create_index("ix_dispute_parties_case_id", "dispute_parties", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_parties_case_id", table_name="dispute_parties")
    op.drop_table("dispute_parties")
