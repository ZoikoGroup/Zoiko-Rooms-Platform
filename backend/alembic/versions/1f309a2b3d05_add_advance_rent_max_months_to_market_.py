"""add advance rent max months to market policy pack

Revision ID: 1f309a2b3d05
Revises: 6447919563e1
Create Date: 2026-09-21 09:37:16.691164

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f309a2b3d05'
down_revision: Union[str, None] = '6447919563e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_policy_packs",
        sa.Column("advance_rent_max_months", sa.Integer(), nullable=False, server_default="12"),
    )


def downgrade() -> None:
    op.drop_column("market_policy_packs", "advance_rent_max_months")
