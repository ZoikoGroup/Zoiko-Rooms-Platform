"""add rental_payment_returns (deposit returns and cancellation refunds paid directly)

Revision ID: c4d8e2f6a1b3
Revises: b7e3c1a9d5f2
Create Date: 2026-09-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d8e2f6a1b3'
down_revision: Union[str, None] = 'b7e3c1a9d5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rental_payment_returns',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('occupancy_id', sa.Integer(), sa.ForeignKey('occupancies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='RECORDED'),
        sa.Column('tenant_guest_id', sa.String(length=20), sa.ForeignKey('guests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('recipient_party_id', sa.Integer(), sa.ForeignKey('parties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('deductions_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('deductions_reason', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('payment_method_category', sa.String(length=20), nullable=False),
        sa.Column('external_reference', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('returned_date', sa.Date(), nullable=False),
        sa.Column('note', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('tenant_responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tenant_dispute_details', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_rental_payment_returns_occupancy_id', 'rental_payment_returns', ['occupancy_id'])
    op.create_index('ix_rental_payment_returns_tenant_guest_id', 'rental_payment_returns', ['tenant_guest_id'])
    op.create_index('ix_rental_payment_returns_recipient_party_id', 'rental_payment_returns', ['recipient_party_id'])


def downgrade() -> None:
    op.drop_index('ix_rental_payment_returns_recipient_party_id', table_name='rental_payment_returns')
    op.drop_index('ix_rental_payment_returns_tenant_guest_id', table_name='rental_payment_returns')
    op.drop_index('ix_rental_payment_returns_occupancy_id', table_name='rental_payment_returns')
    op.drop_table('rental_payment_returns')
