"""add dispute case reopen fields

ZR-ENG-CLR-010 Phase 5: reopen-event columns on dispute_resolution_cases
(closed_at/closed_by_admin_id stay untouched by a reopen -- AC-31), see
app/models/dispute.py's DisputeResolutionCase docstring.

Revision ID: 2ec9edde854e
Revises: 3137a3b80ea5
Create Date: 2026-09-15 00:00:05.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2ec9edde854e'
down_revision: Union[str, None] = '3137a3b80ea5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_resolution_cases", sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dispute_resolution_cases", sa.Column("reopened_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True))
    op.add_column("dispute_resolution_cases", sa.Column("reopen_grounds", sa.String(length=30), nullable=True))
    op.add_column("dispute_resolution_cases", sa.Column("reopen_note", sa.String(length=1000), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("dispute_resolution_cases", "reopen_note")
    op.drop_column("dispute_resolution_cases", "reopen_grounds")
    op.drop_column("dispute_resolution_cases", "reopened_by_admin_id")
    op.drop_column("dispute_resolution_cases", "reopened_at")
