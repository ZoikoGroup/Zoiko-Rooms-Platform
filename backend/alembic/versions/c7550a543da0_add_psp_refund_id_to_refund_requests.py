"""add psp refund id to refund requests

Revision ID: c7550a543da0
Revises: 37d8aabc0798
Create Date: 2026-09-21 09:09:10.078282

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7550a543da0'
down_revision: Union[str, None] = '37d8aabc0798'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "refund_requests",
        sa.Column("psp_refund_id", sa.String(length=100), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("refund_requests", "psp_refund_id")
