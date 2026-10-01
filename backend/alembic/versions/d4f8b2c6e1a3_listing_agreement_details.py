"""listing agreement details: host-supplied Residential Occupancy Agreement facts

Revision ID: d4f8b2c6e1a3
Revises: c3e7a9d1f5b2
Create Date: 2026-10-01 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f8b2c6e1a3'
down_revision: Union[str, None] = 'c3e7a9d1f5b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "listings",
        sa.Column("agreement_details", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("listings", "agreement_details")
