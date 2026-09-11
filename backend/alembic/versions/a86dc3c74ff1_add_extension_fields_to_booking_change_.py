"""add extension fields to booking_change_requests

Revision ID: a86dc3c74ff1
Revises: 5cdcb0af9c8b
Create Date: 2026-09-10 11:28:28.243007

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a86dc3c74ff1'
down_revision: Union[str, None] = '5cdcb0af9c8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('booking_change_requests', sa.Column('original_end_date', sa.Date(), nullable=True))
    op.add_column('booking_change_requests', sa.Column('proposed_end_date', sa.Date(), nullable=True))
    op.add_column('booking_change_requests', sa.Column('additional_term_months', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('booking_change_requests', 'additional_term_months')
    op.drop_column('booking_change_requests', 'proposed_end_date')
    op.drop_column('booking_change_requests', 'original_end_date')
