"""add listing fee domain

Revision ID: 8f1a2c3d4e5b
Revises: 294dc9f9f823
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f1a2c3d4e5b'
down_revision: Union[str, None] = '294dc9f9f823'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-PAY-002 Section 8/12.1: the Listing Fee domain -- the only payment
    # Zoiko Rooms collects for itself, kept fully separate from
    # models/finance.py's rent/deposit/payout tables (no shared FKs).
    op.create_table(
        'listing_fee_policies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('jurisdiction_code', sa.String(length=10), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('tax_rate', sa.Numeric(6, 4), nullable=False, server_default='0.0'),
        sa.Column('quote_validity_minutes', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('legal_entity_name', sa.String(length=200), nullable=False),
        sa.Column('tax_registration_number', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('disclosure_text', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_listing_fee_policies_jurisdiction_code', 'listing_fee_policies', ['jurisdiction_code'])

    op.create_table(
        'listing_fee_quotes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('listing_id', sa.String(length=20), sa.ForeignKey('listings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('party_id', sa.Integer(), sa.ForeignKey('parties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('tax_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('total_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('policy_snapshot', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_listing_fee_quotes_listing_id', 'listing_fee_quotes', ['listing_id'])
    op.create_index('ix_listing_fee_quotes_party_id', 'listing_fee_quotes', ['party_id'])

    op.create_table(
        'listing_fee_payments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('quote_id', sa.Integer(), sa.ForeignKey('listing_fee_quotes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('listing_id', sa.String(length=20), sa.ForeignKey('listings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('party_id', sa.Integer(), sa.ForeignKey('parties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('billing_country', sa.String(length=2), nullable=False, server_default=''),
        sa.Column('provider_payment_intent_id', sa.String(length=100), nullable=True),
        sa.Column('failure_message', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_listing_fee_payments_quote_id', 'listing_fee_payments', ['quote_id'])
    op.create_index('ix_listing_fee_payments_listing_id', 'listing_fee_payments', ['listing_id'])
    op.create_index('ix_listing_fee_payments_party_id', 'listing_fee_payments', ['party_id'])
    op.create_index('uq_listing_fee_payments_idempotency_key', 'listing_fee_payments', ['idempotency_key'], unique=True)
    op.create_index(
        'uq_listing_fee_payments_provider_intent', 'listing_fee_payments', ['provider_payment_intent_id'], unique=True,
    )

    op.create_table(
        'listing_fee_receipts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('payment_id', sa.Integer(), sa.ForeignKey('listing_fee_payments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('receipt_number', sa.String(length=30), nullable=False),
        sa.Column('legal_entity_name', sa.String(length=200), nullable=False),
        sa.Column('tax_registration_number', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('tax_rate', sa.Numeric(6, 4), nullable=False),
        sa.Column('tax_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('total_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('storage_ref', sa.String(length=255), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('uq_listing_fee_receipts_payment_id', 'listing_fee_receipts', ['payment_id'], unique=True)
    op.create_index('uq_listing_fee_receipts_receipt_number', 'listing_fee_receipts', ['receipt_number'], unique=True)

    op.create_table(
        'listing_fee_refunds',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('payment_id', sa.Integer(), sa.ForeignKey('listing_fee_payments.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('reason', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='REQUESTED'),
        sa.Column('idempotency_key', sa.String(length=255), nullable=False),
        sa.Column('provider_refund_id', sa.String(length=100), nullable=True),
        sa.Column('requested_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=False),
        sa.Column('failure_message', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_listing_fee_refunds_payment_id', 'listing_fee_refunds', ['payment_id'])
    op.create_index('uq_listing_fee_refunds_idempotency_key', 'listing_fee_refunds', ['idempotency_key'], unique=True)
    op.create_index('uq_listing_fee_refunds_provider_refund_id', 'listing_fee_refunds', ['provider_refund_id'], unique=True)

    op.create_table(
        'listing_fee_provider_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider_event_id', sa.String(length=100), nullable=False),
        sa.Column('listing_fee_payment_id', sa.Integer(), sa.ForeignKey('listing_fee_payments.id'), nullable=True),
        sa.Column('listing_fee_refund_id', sa.Integer(), sa.ForeignKey('listing_fee_refunds.id'), nullable=True),
        sa.Column('event_type', sa.String(length=30), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'uq_listing_fee_provider_events_event_id', 'listing_fee_provider_events', ['provider_event_id'], unique=True,
    )


def downgrade() -> None:
    op.drop_table('listing_fee_provider_events')
    op.drop_index('uq_listing_fee_refunds_provider_refund_id', table_name='listing_fee_refunds')
    op.drop_index('uq_listing_fee_refunds_idempotency_key', table_name='listing_fee_refunds')
    op.drop_index('ix_listing_fee_refunds_payment_id', table_name='listing_fee_refunds')
    op.drop_table('listing_fee_refunds')
    op.drop_index('uq_listing_fee_receipts_receipt_number', table_name='listing_fee_receipts')
    op.drop_index('uq_listing_fee_receipts_payment_id', table_name='listing_fee_receipts')
    op.drop_table('listing_fee_receipts')
    op.drop_index('uq_listing_fee_payments_provider_intent', table_name='listing_fee_payments')
    op.drop_index('uq_listing_fee_payments_idempotency_key', table_name='listing_fee_payments')
    op.drop_index('ix_listing_fee_payments_party_id', table_name='listing_fee_payments')
    op.drop_index('ix_listing_fee_payments_listing_id', table_name='listing_fee_payments')
    op.drop_index('ix_listing_fee_payments_quote_id', table_name='listing_fee_payments')
    op.drop_table('listing_fee_payments')
    op.drop_index('ix_listing_fee_quotes_party_id', table_name='listing_fee_quotes')
    op.drop_index('ix_listing_fee_quotes_listing_id', table_name='listing_fee_quotes')
    op.drop_table('listing_fee_quotes')
    op.drop_index('ix_listing_fee_policies_jurisdiction_code', table_name='listing_fee_policies')
    op.drop_table('listing_fee_policies')
