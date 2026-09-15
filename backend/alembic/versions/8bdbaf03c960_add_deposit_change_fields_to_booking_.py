"""add deposit change fields to booking change requests

Revision ID: 8bdbaf03c960
Revises: 01cf9939f46e
Create Date: 2026-09-11 11:57:51.154503

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8bdbaf03c960'
down_revision: Union[str, None] = '01cf9939f46e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("booking_change_requests", sa.Column("original_deposit_amount", sa.Numeric(10, 2), nullable=True))
    op.add_column("booking_change_requests", sa.Column("proposed_deposit_amount", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("booking_change_requests", "proposed_deposit_amount")
    op.drop_column("booking_change_requests", "original_deposit_amount")
