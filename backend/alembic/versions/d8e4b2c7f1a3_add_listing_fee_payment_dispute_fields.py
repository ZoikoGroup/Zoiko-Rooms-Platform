"""add chargeback (dispute) fields to listing fee payments

Revision ID: d8e4b2c7f1a3
Revises: c3a9f6e2d8b4
Create Date: 2026-09-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e4b2c7f1a3'
down_revision: Union[str, None] = 'c3a9f6e2d8b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listing_fee_payments', sa.Column('dispute_status', sa.String(length=20), nullable=True))
    op.add_column('listing_fee_payments', sa.Column('provider_dispute_id', sa.String(length=100), nullable=True))
    op.add_column('listing_fee_payments', sa.Column('disputed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('listing_fee_payments', 'disputed_at')
    op.drop_column('listing_fee_payments', 'provider_dispute_id')
    op.drop_column('listing_fee_payments', 'dispute_status')
