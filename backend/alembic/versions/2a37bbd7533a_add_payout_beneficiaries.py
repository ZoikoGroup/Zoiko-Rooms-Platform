"""add payout_beneficiaries

Revision ID: 2a37bbd7533a
Revises: 1925e4055f63
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a37bbd7533a'
down_revision: Union[str, None] = '1925e4055f63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-005 AC-30/Section 9.2: a Party's verified payout destination
    # -- see models/finance.py:PayoutBeneficiary. At most one VERIFIED row per
    # party (partial unique index below); run_payout reads that row as a new
    # payout eligibility gate.
    op.create_table(
        'payout_beneficiaries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('party_id', sa.Integer(), sa.ForeignKey('parties.id', ondelete='CASCADE'), nullable=False),
        sa.Column('account_holder_name', sa.String(length=200), nullable=False),
        sa.Column('bank_name', sa.String(length=200), nullable=False),
        sa.Column('account_number_last4', sa.String(length=4), nullable=False),
        sa.Column('ifsc_code', sa.String(length=11), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING_VERIFICATION'),
        sa.Column('verification_code_hash', sa.String(length=64), nullable=True),
        sa.Column('verification_code_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verification_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_payout_beneficiaries_party_id', 'payout_beneficiaries', ['party_id'])
    op.create_index(
        'uq_payout_beneficiaries_verified_party', 'payout_beneficiaries', ['party_id'],
        unique=True, postgresql_where=sa.text("status = 'VERIFIED'"), sqlite_where=sa.text("status = 'VERIFIED'"),
    )


def downgrade() -> None:
    op.drop_index('uq_payout_beneficiaries_verified_party', table_name='payout_beneficiaries')
    op.drop_index('ix_payout_beneficiaries_party_id', table_name='payout_beneficiaries')
    op.drop_table('payout_beneficiaries')
