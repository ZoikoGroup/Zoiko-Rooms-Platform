"""add rental payment provider account change-governance columns

Revision ID: c46f26ba28bd
Revises: a86e7241e1af
Create Date: 2026-09-22 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c46f26ba28bd'
down_revision: Union[str, None] = 'a86e7241e1af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('uq_rental_payment_provider_accounts_party', 'rental_payment_provider_accounts', type_='unique')
    op.alter_column(
        'rental_payment_provider_accounts', 'status',
        existing_type=sa.String(length=20), type_=sa.String(length=30), existing_nullable=False,
    )
    op.add_column('rental_payment_provider_accounts', sa.Column('verification_code_hash', sa.String(length=64), nullable=True))
    op.add_column('rental_payment_provider_accounts', sa.Column('verification_code_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('rental_payment_provider_accounts', sa.Column('verification_attempts', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('rental_payment_provider_accounts', sa.Column('is_high_risk', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('rental_payment_provider_accounts', sa.Column('high_risk_reason', sa.String(length=255), nullable=False, server_default=''))
    op.create_index(
        'uq_rental_payment_provider_accounts_active_party', 'rental_payment_provider_accounts', ['party_id'], unique=True,
        postgresql_where=sa.text("status != 'SUPERSEDED'"), sqlite_where=sa.text("status != 'SUPERSEDED'"),
    )


def downgrade() -> None:
    op.drop_index('uq_rental_payment_provider_accounts_active_party', table_name='rental_payment_provider_accounts')
    op.drop_column('rental_payment_provider_accounts', 'high_risk_reason')
    op.drop_column('rental_payment_provider_accounts', 'is_high_risk')
    op.drop_column('rental_payment_provider_accounts', 'verification_attempts')
    op.drop_column('rental_payment_provider_accounts', 'verification_code_expires_at')
    op.drop_column('rental_payment_provider_accounts', 'verification_code_hash')
    op.alter_column(
        'rental_payment_provider_accounts', 'status',
        existing_type=sa.String(length=30), type_=sa.String(length=20), existing_nullable=False,
    )
    op.create_unique_constraint('uq_rental_payment_provider_accounts_party', 'rental_payment_provider_accounts', ['party_id'])
