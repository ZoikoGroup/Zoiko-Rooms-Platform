"""add notice service proof fields to termination case

Revision ID: 3259cd2709c6
Revises: ae9441eb141f
Create Date: 2026-09-21 11:32:52.989822

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3259cd2709c6'
down_revision: Union[str, None] = 'ae9441eb141f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "termination_cases",
        sa.Column("notice_service_proof_ref", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column(
        "termination_cases",
        sa.Column("notice_service_recorded_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("termination_cases", "notice_service_recorded_by_admin_id")
    op.drop_column("termination_cases", "notice_service_proof_ref")
