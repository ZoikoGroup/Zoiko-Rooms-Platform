"""add market_policy_packs.platform_fee_rate

Revision ID: fd2dc05f06e5
Revises: 5420bcaed552
Create Date: 2026-09-10 11:55:15.934075

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd2dc05f06e5'
down_revision: Union[str, None] = '5420bcaed552'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-005 AC-09: platform fees resolved from effective-dated
    # policy, not a hard-coded PLATFORM_FEE_RATE constant. server_default
    # matches the constant's prior value (0.10 = 10%) so every existing
    # policy pack row backfills to the exact same rate crud/finance.py:
    # run_payout already computed -- no behavior change from this migration
    # alone.
    op.add_column(
        'market_policy_packs', sa.Column('platform_fee_rate', sa.Numeric(6, 4), nullable=False, server_default='0.10'),
    )


def downgrade() -> None:
    op.drop_column('market_policy_packs', 'platform_fee_rate')
