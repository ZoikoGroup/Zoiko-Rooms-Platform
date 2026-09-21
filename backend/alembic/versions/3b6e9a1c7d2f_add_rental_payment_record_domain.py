"""add rental payment record domain

Revision ID: 3b6e9a1c7d2f
Revises: 8f1a2c3d4e5b
Create Date: 2026-09-21 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b6e9a1c7d2f'
down_revision: Union[str, None] = '8f1a2c3d4e5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-PAY-002 Section 4-7/12.1: obligations, declarations, confirmations,
    # disputes and corrections -- never a custody/settlement table. Kept
    # independent of models/finance.py's Obligation/SimulatedPayment (no
    # shared FKs); see models/rental_payment.py's own module docstring.
    op.create_table(
        'rental_payment_obligations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('obligation_type', sa.String(length=20), nullable=False),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=True),
        sa.Column('occupancy_id', sa.Integer(), sa.ForeignKey('occupancies.id', ondelete='CASCADE'), nullable=True),
        sa.Column('tenant_guest_id', sa.String(length=20), sa.ForeignKey('guests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('recipient_party_id', sa.Integer(), sa.ForeignKey('parties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='UPCOMING'),
        sa.Column('waived_reason', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('waived_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('waived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_rental_payment_obligations_agreement_id', 'rental_payment_obligations', ['agreement_id'])
    op.create_index('ix_rental_payment_obligations_occupancy_id', 'rental_payment_obligations', ['occupancy_id'])
    op.create_index('ix_rental_payment_obligations_tenant_guest_id', 'rental_payment_obligations', ['tenant_guest_id'])
    op.create_index('ix_rental_payment_obligations_recipient_party_id', 'rental_payment_obligations', ['recipient_party_id'])

    op.create_table(
        'rental_payment_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('obligation_id', sa.Integer(), sa.ForeignKey('rental_payment_obligations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False, server_default='TENANT_MARKED_PAID'),
        sa.Column('provenance', sa.String(length=30), nullable=False, server_default='TENANT_DECLARATION'),
        sa.Column('declared_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('declared_currency', sa.String(length=3), nullable=False),
        sa.Column('declared_date', sa.Date(), nullable=False),
        sa.Column('payment_method_category', sa.String(length=20), nullable=False, server_default='OTHER'),
        sa.Column('external_reference', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('declared_by_guest_id', sa.String(length=20), sa.ForeignKey('guests.id', ondelete='CASCADE'), nullable=False),
        sa.Column('confirmed_by_party_id', sa.Integer(), sa.ForeignKey('parties.id'), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_rental_payment_records_obligation_id', 'rental_payment_records', ['obligation_id'])

    op.create_table(
        'rental_payment_disputes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('record_id', sa.Integer(), sa.ForeignKey('rental_payment_records.id', ondelete='CASCADE'), nullable=False),
        sa.Column('reason_code', sa.String(length=30), nullable=False),
        sa.Column('details', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='OPEN'),
        sa.Column('reported_by_guest_id', sa.String(length=20), sa.ForeignKey('guests.id'), nullable=True),
        sa.Column('reported_by_party_id', sa.Integer(), sa.ForeignKey('parties.id'), nullable=True),
        sa.Column('reported_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_notes', sa.String(length=2000), nullable=False, server_default=''),
    )
    op.create_index('ix_rental_payment_disputes_record_id', 'rental_payment_disputes', ['record_id'])

    op.create_table(
        'rental_payment_corrections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('record_id', sa.Integer(), sa.ForeignKey('rental_payment_records.id', ondelete='CASCADE'), nullable=False),
        sa.Column('field_name', sa.String(length=50), nullable=False),
        sa.Column('previous_value', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('new_value', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('reason', sa.String(length=2000), nullable=False),
        sa.Column('actor_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_rental_payment_corrections_record_id', 'rental_payment_corrections', ['record_id'])


def downgrade() -> None:
    op.drop_index('ix_rental_payment_corrections_record_id', table_name='rental_payment_corrections')
    op.drop_table('rental_payment_corrections')
    op.drop_index('ix_rental_payment_disputes_record_id', table_name='rental_payment_disputes')
    op.drop_table('rental_payment_disputes')
    op.drop_index('ix_rental_payment_records_obligation_id', table_name='rental_payment_records')
    op.drop_table('rental_payment_records')
    op.drop_index('ix_rental_payment_obligations_recipient_party_id', table_name='rental_payment_obligations')
    op.drop_index('ix_rental_payment_obligations_tenant_guest_id', table_name='rental_payment_obligations')
    op.drop_index('ix_rental_payment_obligations_occupancy_id', table_name='rental_payment_obligations')
    op.drop_index('ix_rental_payment_obligations_agreement_id', table_name='rental_payment_obligations')
    op.drop_table('rental_payment_obligations')
