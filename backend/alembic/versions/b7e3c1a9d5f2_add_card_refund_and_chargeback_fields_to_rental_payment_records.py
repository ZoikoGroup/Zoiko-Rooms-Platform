"""add card refund and chargeback fields to rental payment records

Revision ID: b7e3c1a9d5f2
Revises: 2ed3da1de0da
Create Date: 2026-09-26 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e3c1a9d5f2'
down_revision: Union[str, None] = '2ed3da1de0da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('rental_payment_records', sa.Column('refunded_amount', sa.Numeric(12, 2), nullable=True))
    op.add_column('rental_payment_records', sa.Column('provider_refund_id', sa.String(length=255), nullable=False, server_default=''))
    op.add_column('rental_payment_records', sa.Column('provider_dispute_id', sa.String(length=255), nullable=False, server_default=''))
    op.add_column('rental_payment_records', sa.Column('provider_dispute_status', sa.String(length=30), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('rental_payment_records', 'provider_dispute_status')
    op.drop_column('rental_payment_records', 'provider_dispute_id')
    op.drop_column('rental_payment_records', 'provider_refund_id')
    op.drop_column('rental_payment_records', 'refunded_amount')
