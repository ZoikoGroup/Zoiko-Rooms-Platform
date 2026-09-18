"""add ledger accounts and entries

Revision ID: 5420bcaed552
Revises: 24b0126104f7
Create Date: 2026-09-10 11:13:49.664256

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5420bcaed552'
down_revision: Union[str, None] = '24b0126104f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ledger_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_type', sa.String(length=30), nullable=False),
        sa.Column('party_id', sa.Integer(), sa.ForeignKey('parties.id', ondelete='CASCADE'), nullable=True),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('account_type', 'party_id', 'currency', name='uq_ledger_account_type_party_currency'),
    )
    op.create_table(
        'ledger_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('debit_account_id', sa.Integer(), sa.ForeignKey('ledger_accounts.id'), nullable=False),
        sa.Column('credit_account_id', sa.Integer(), sa.ForeignKey('ledger_accounts.id'), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('description', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('source_type', sa.String(length=30), nullable=False),
        sa.Column('source_id', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('ledger_entries')
    op.drop_table('ledger_accounts')
