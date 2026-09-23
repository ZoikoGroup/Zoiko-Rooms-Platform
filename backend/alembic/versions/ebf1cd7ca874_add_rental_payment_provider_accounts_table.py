"""add rental_payment_provider_accounts table

Revision ID: ebf1cd7ca874
Revises: 0b6d187351e9
Create Date: 2026-09-22 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'ebf1cd7ca874'
down_revision: Union[str, None] = '0b6d187351e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'rental_payment_provider_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('party_id', sa.Integer(), nullable=False),
        sa.Column('stripe_account_id', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('details_submitted', sa.Boolean(), nullable=False),
        sa.Column('charges_enabled', sa.Boolean(), nullable=False),
        sa.Column('payouts_enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['party_id'], ['parties.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('party_id', name='uq_rental_payment_provider_accounts_party'),
        sa.UniqueConstraint('stripe_account_id'),
    )
    op.create_index(
        op.f('ix_rental_payment_provider_accounts_party_id'), 'rental_payment_provider_accounts', ['party_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_rental_payment_provider_accounts_party_id'), table_name='rental_payment_provider_accounts')
    op.drop_table('rental_payment_provider_accounts')
