"""add rental_payment_records confirmed_amount and provider_reference

Revision ID: 7a3f9c2e5b1d
Revises: 2e7c5a9f1d3b
Create Date: 2026-09-21 05:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a3f9c2e5b1d'
down_revision: Union[str, None] = '2e7c5a9f1d3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-PAY-002 Section 6: PARTIALLY_PAID needs a confirmed amount distinct
    # from the tenant's own declared_amount.
    op.add_column('rental_payment_records', sa.Column('confirmed_amount', sa.Numeric(12, 2), nullable=True))
    # ZR-PAY-002 Section 12.1's shared 'provider_event_reference' object --
    # the external provider transaction/reconciliation reference a
    # CONFIRMED_BY_PROVIDER status is backed by.
    op.add_column('rental_payment_records', sa.Column('provider_reference', sa.String(length=255), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('rental_payment_records', 'provider_reference')
    op.drop_column('rental_payment_records', 'confirmed_amount')
