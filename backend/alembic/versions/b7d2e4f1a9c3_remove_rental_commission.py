"""remove the rental commission: platform_fee_rate becomes an always-null column

Revision ID: b7d2e4f1a9c3
Revises: 9c2e7a4d1b6f
Create Date: 2026-09-24 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2e4f1a9c3'
down_revision: Union[str, None] = '9c2e7a4d1b6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-PAY-CFG-001 Decision 3: "Migrate any existing Zoiko Rooms 10%
    # test/default value to null before production deployment."
    with op.batch_alter_table('market_policy_packs') as batch_op:
        batch_op.alter_column('platform_fee_rate', existing_type=sa.Numeric(6, 4), nullable=True)
    op.execute("UPDATE market_policy_packs SET platform_fee_rate = NULL")


def downgrade() -> None:
    op.execute("UPDATE market_policy_packs SET platform_fee_rate = 0.10 WHERE platform_fee_rate IS NULL")
    with op.batch_alter_table('market_policy_packs') as batch_op:
        batch_op.alter_column('platform_fee_rate', existing_type=sa.Numeric(6, 4), nullable=False)
