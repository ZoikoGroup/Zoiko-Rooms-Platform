"""add service_fee_invoices and market_policy_packs entity/tax fields

Revision ID: ec2c29b24747
Revises: 6892b7bf21a7
Create Date: 2026-09-10 16:37:10.733590

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec2c29b24747'
down_revision: Union[str, None] = '6892b7bf21a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'market_policy_packs',
        sa.Column('zoiko_legal_entity_name', sa.String(length=200), nullable=False, server_default='Zoiko Realty Group'),
    )
    op.add_column(
        'market_policy_packs', sa.Column('zoiko_tax_registration_number', sa.String(length=50), nullable=False, server_default=''),
    )
    op.add_column(
        'market_policy_packs', sa.Column('service_fee_tax_rate', sa.Numeric(6, 4), nullable=False, server_default='0.0'),
    )

    op.create_table(
        'service_fee_invoices',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'payout_id', sa.Integer(), sa.ForeignKey('payout_records.id', ondelete='CASCADE'),
            nullable=False, unique=True,
        ),
        sa.Column('invoice_number', sa.String(length=30), nullable=False, unique=True),
        sa.Column('legal_entity_name', sa.String(length=200), nullable=False),
        sa.Column('tax_registration_number', sa.String(length=50), nullable=False, server_default=''),
        sa.Column('fee_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('tax_rate', sa.Numeric(6, 4), nullable=False),
        sa.Column('tax_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('storage_ref', sa.String(length=255), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('service_fee_invoices')
    op.drop_column('market_policy_packs', 'service_fee_tax_rate')
    op.drop_column('market_policy_packs', 'zoiko_tax_registration_number')
    op.drop_column('market_policy_packs', 'zoiko_legal_entity_name')
