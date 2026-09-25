"""listing fee Price Book fields and the Billing Entity Registry (ZR-PAY-CFG-001)

Revision ID: c3a9f6e2d8b4
Revises: b7d2e4f1a9c3
Create Date: 2026-09-24 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a9f6e2d8b4'
down_revision: Union[str, None] = 'b7d2e4f1a9c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ZERO_DECIMAL_CURRENCIES = (
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
)


def upgrade() -> None:
    op.create_table(
        'billing_entities',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(length=40), nullable=False, unique=True),
        sa.Column('legal_name', sa.String(length=200), nullable=False),
        sa.Column('trading_name', sa.String(length=200), nullable=False, server_default='Zoiko Rooms'),
        sa.Column('registered_address', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('company_registration_number', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('tax_registration_type', sa.String(length=40), nullable=False, server_default=''),
        sa.Column('tax_registration_number', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('supported_markets', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('supported_currencies', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='ACTIVE'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )

    with op.batch_alter_table('listing_fee_policies') as batch_op:
        batch_op.add_column(sa.Column('amount_minor', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('status', sa.String(length=20), nullable=False, server_default='ACTIVE'))
        batch_op.add_column(sa.Column('tax_behavior', sa.String(length=20), nullable=False, server_default='EXCLUSIVE'))
        batch_op.add_column(sa.Column('tax_rule_reference', sa.String(length=200), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('billing_entity_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('environment', sa.String(length=20), nullable=False, server_default='development'))
        batch_op.add_column(sa.Column('created_by_admin_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('approved_by_admin_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key('fk_listing_fee_policies_billing_entity_id', 'billing_entities', ['billing_entity_id'], ['id'])
        batch_op.create_foreign_key('fk_listing_fee_policies_created_by_admin_id', 'admin_users', ['created_by_admin_id'], ['id'])
        batch_op.create_foreign_key('fk_listing_fee_policies_approved_by_admin_id', 'admin_users', ['approved_by_admin_id'], ['id'])

    zero_decimal = ", ".join(f"'{c}'" for c in ZERO_DECIMAL_CURRENCIES)
    op.execute(
        "UPDATE listing_fee_policies SET amount_minor = CASE "
        f"WHEN upper(currency) IN ({zero_decimal}) THEN ROUND(amount) ELSE ROUND(amount * 100) END"
    )

    with op.batch_alter_table('listing_fee_quotes') as batch_op:
        batch_op.add_column(sa.Column('amount_minor', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('tax_amount_minor', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('total_amount_minor', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('listing_fee_quotes') as batch_op:
        batch_op.drop_column('total_amount_minor')
        batch_op.drop_column('tax_amount_minor')
        batch_op.drop_column('amount_minor')

    with op.batch_alter_table('listing_fee_policies') as batch_op:
        batch_op.drop_constraint('fk_listing_fee_policies_approved_by_admin_id', type_='foreignkey')
        batch_op.drop_constraint('fk_listing_fee_policies_created_by_admin_id', type_='foreignkey')
        batch_op.drop_constraint('fk_listing_fee_policies_billing_entity_id', type_='foreignkey')
        for column in (
            'approved_at', 'approved_by_admin_id', 'created_by_admin_id', 'environment', 'billing_entity_id',
            'tax_rule_reference', 'tax_behavior', 'status', 'amount_minor',
        ):
            batch_op.drop_column(column)

    op.drop_table('billing_entities')
