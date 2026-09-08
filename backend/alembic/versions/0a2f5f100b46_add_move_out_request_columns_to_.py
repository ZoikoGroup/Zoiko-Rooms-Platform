"""add move out request columns to occupancies

Revision ID: 0a2f5f100b46
Revises: e30b58a0360a
Create Date: 2026-09-07 13:47:00.140252

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a2f5f100b46'
down_revision: Union[str, None] = 'e30b58a0360a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("occupancies", sa.Column("requested_move_out_date", sa.Date(), nullable=True))
    op.add_column("occupancies", sa.Column("move_out_requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("occupancies", "move_out_requested_at")
    op.drop_column("occupancies", "requested_move_out_date")
