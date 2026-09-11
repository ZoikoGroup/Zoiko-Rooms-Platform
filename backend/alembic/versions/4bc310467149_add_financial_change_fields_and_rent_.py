"""add financial change fields and rent change policy

Revision ID: 4bc310467149
Revises: 458ec13890f5
Create Date: 2026-09-10 17:19:59.320984

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4bc310467149'
down_revision: Union[str, None] = '458ec13890f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('booking_change_requests', sa.Column('original_monthly_rent', sa.Numeric(10, 2), nullable=True))
    op.add_column('booking_change_requests', sa.Column('proposed_monthly_rent', sa.Numeric(10, 2), nullable=True))
    op.add_column('market_policy_packs', sa.Column('rent_change_min_interval_days', sa.Integer(), nullable=False, server_default='365'))


def downgrade() -> None:
    op.drop_column('market_policy_packs', 'rent_change_min_interval_days')
    op.drop_column('booking_change_requests', 'proposed_monthly_rent')
    op.drop_column('booking_change_requests', 'original_monthly_rent')
