"""add custom cadence interval

Revision ID: d45a37ff54df
Revises: 2a37bbd7533a
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd45a37ff54df'
down_revision: Union[str, None] = '2a37bbd7533a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-005 AC-06: UPFRONT/CUSTOM cadences -- CUSTOM's admin-specified
    # billing interval in days, carried from offer_terms into the
    # PaymentSchedule it seeds. Null for every other cadence, including
    # UPFRONT (which has no recurring interval at all).
    op.add_column('offer_terms', sa.Column('custom_interval_days', sa.Integer(), nullable=True))
    op.add_column('payment_schedules', sa.Column('custom_interval_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('payment_schedules', 'custom_interval_days')
    op.drop_column('offer_terms', 'custom_interval_days')
