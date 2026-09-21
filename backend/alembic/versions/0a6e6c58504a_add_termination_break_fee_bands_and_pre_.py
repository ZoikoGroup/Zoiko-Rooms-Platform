"""add termination break fee bands and pre move in cancellation policy

Revision ID: 0a6e6c58504a
Revises: 1f309a2b3d05
Create Date: 2026-09-21 10:08:17.894402

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a6e6c58504a'
down_revision: Union[str, None] = '1f309a2b3d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_policy_packs",
        sa.Column("termination_break_fee_bands", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("pre_move_in_free_cancellation_hours", sa.Integer(), nullable=False, server_default="24"),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("pre_move_in_cancellation_fee_rent_multiple", sa.Numeric(6, 2), nullable=False, server_default="0.0"),
    )


def downgrade() -> None:
    op.drop_column("market_policy_packs", "pre_move_in_cancellation_fee_rent_multiple")
    op.drop_column("market_policy_packs", "pre_move_in_free_cancellation_hours")
    op.drop_column("market_policy_packs", "termination_break_fee_bands")
