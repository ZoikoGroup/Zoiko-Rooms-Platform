"""add listing_fee_policies refund_eligible and refund_window_days

Revision ID: 4d8b6e1a9c3f
Revises: 7a3f9c2e5b1d
Create Date: 2026-09-21 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4d8b6e1a9c3f'
down_revision: Union[str, None] = '7a3f9c2e5b1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-PAY-002 Section 8.4: REFUND_ELIGIBLE -- 'Display only when
    # commercial policy/jurisdiction configuration permits.'
    op.add_column('listing_fee_policies', sa.Column('refund_eligible', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('listing_fee_policies', sa.Column('refund_window_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('listing_fee_policies', 'refund_window_days')
    op.drop_column('listing_fee_policies', 'refund_eligible')
