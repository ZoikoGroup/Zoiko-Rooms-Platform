"""add external_payment_sessions and rental_payment_provider_events tables

Revision ID: a86e7241e1af
Revises: ebf1cd7ca874
Create Date: 2026-09-22 17:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a86e7241e1af'
down_revision: Union[str, None] = 'ebf1cd7ca874'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'external_payment_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('obligation_id', sa.Integer(), nullable=False),
        sa.Column('tenant_guest_id', sa.String(length=20), nullable=False),
        sa.Column('recipient_stripe_account_id', sa.String(length=100), nullable=False),
        sa.Column('provider_checkout_session_id', sa.String(length=100), nullable=False),
        sa.Column('provider_payment_intent_id', sa.String(length=100), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('failure_message', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['obligation_id'], ['rental_payment_obligations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_guest_id'], ['guests.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider_checkout_session_id'),
    )
    op.create_index(
        op.f('ix_external_payment_sessions_obligation_id'), 'external_payment_sessions', ['obligation_id'], unique=False,
    )

    op.create_table(
        'rental_payment_provider_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('provider_event_id', sa.String(length=100), nullable=False),
        sa.Column('external_payment_session_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(length=50), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['external_payment_session_id'], ['external_payment_sessions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider_event_id'),
    )


def downgrade() -> None:
    op.drop_table('rental_payment_provider_events')
    op.drop_index(op.f('ix_external_payment_sessions_obligation_id'), table_name='external_payment_sessions')
    op.drop_table('external_payment_sessions')
