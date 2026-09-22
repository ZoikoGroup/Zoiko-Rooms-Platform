"""add rental payment instructions

Revision ID: 5c8d3f0a1b4e
Revises: 3b6e9a1c7d2f
Create Date: 2026-09-21 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c8d3f0a1b4e'
down_revision: Union[str, None] = '3b6e9a1c7d2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-PAY-002 Section 9: a landlord/agent's own payment details for
    # receiving rent/deposit directly -- see models/rental_payment.py:
    # RentalPaymentInstruction's own docstring.
    op.create_table(
        'rental_payment_instructions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('party_id', sa.Integer(), sa.ForeignKey('parties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING_VERIFICATION'),
        sa.Column('method', sa.String(length=20), nullable=False, server_default='BANK_TRANSFER'),
        sa.Column('recipient_name', sa.String(length=200), nullable=False),
        sa.Column('account_identifier_last4', sa.String(length=4), nullable=False),
        sa.Column('reference_format', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('additional_instructions', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('verification_code_hash', sa.String(length=64), nullable=True),
        sa.Column('verification_code_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verification_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_rental_payment_instructions_party_id', 'rental_payment_instructions', ['party_id'])
    op.create_index(
        'uq_rental_payment_instructions_active_party', 'rental_payment_instructions', ['party_id'],
        unique=True, postgresql_where=sa.text("status = 'ACTIVE'"), sqlite_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index('uq_rental_payment_instructions_active_party', table_name='rental_payment_instructions')
    op.drop_index('ix_rental_payment_instructions_party_id', table_name='rental_payment_instructions')
    op.drop_table('rental_payment_instructions')
