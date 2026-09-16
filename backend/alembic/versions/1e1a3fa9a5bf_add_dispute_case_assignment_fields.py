"""add dispute case assignment fields

ZR-ENG-CLR-010 Phase 12: assigned_team/assigned_admin_id routing metadata
on dispute_resolution_cases, see app/models/dispute.py's
DISPUTE_CASE_TEAMS comment.

Revision ID: 1e1a3fa9a5bf
Revises: 3b254bbed5d1
Create Date: 2026-09-15 00:00:09.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e1a3fa9a5bf'
down_revision: Union[str, None] = '3b254bbed5d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_resolution_cases", sa.Column("assigned_team", sa.String(length=20), nullable=True))
    op.add_column("dispute_resolution_cases", sa.Column("assigned_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("dispute_resolution_cases", "assigned_admin_id")
    op.drop_column("dispute_resolution_cases", "assigned_team")
