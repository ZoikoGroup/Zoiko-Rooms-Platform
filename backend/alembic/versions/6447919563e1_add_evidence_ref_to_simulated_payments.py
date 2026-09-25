"""add evidence ref to simulated payments

Revision ID: 6447919563e1
Revises: c7550a543da0
Create Date: 2026-09-21 09:29:34.862012

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6447919563e1'
down_revision: Union[str, None] = 'c7550a543da0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "simulated_payments",
        sa.Column("evidence_ref", sa.String(length=500), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("simulated_payments", "evidence_ref")
