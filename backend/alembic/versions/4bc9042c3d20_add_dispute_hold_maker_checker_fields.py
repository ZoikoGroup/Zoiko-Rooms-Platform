"""add dispute hold maker-checker fields

ZR-ENG-CLR-010 Phase 8: maker-checker columns on dispute_resolution_holds
(status String length widened to fit RELEASE_PENDING), see
app/models/dispute.py's DisputeResolutionHold docstring.

Revision ID: 4bc9042c3d20
Revises: 2ec9edde854e
Create Date: 2026-09-15 00:00:06.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4bc9042c3d20'
down_revision: Union[str, None] = '2ec9edde854e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("dispute_resolution_holds", "status", type_=sa.String(length=15))
    op.add_column("dispute_resolution_holds", sa.Column("approved_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True))
    op.add_column("dispute_resolution_holds", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dispute_resolution_holds", sa.Column("release_requested_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True))
    op.add_column("dispute_resolution_holds", sa.Column("release_requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("dispute_resolution_holds", "release_requested_at")
    op.drop_column("dispute_resolution_holds", "release_requested_by_admin_id")
    op.drop_column("dispute_resolution_holds", "approved_at")
    op.drop_column("dispute_resolution_holds", "approved_by_admin_id")
    op.alter_column("dispute_resolution_holds", "status", type_=sa.String(length=10))
