"""add payout_records.recovery_offset_amount

Revision ID: 1b7e4f92a3c6
Revises: 9d3f6a1c7e52
Create Date: 2026-09-13 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b7e4f92a3c6'
down_revision: Union[str, None] = '9d3f6a1c7e52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 15 waterfall tier 4: how much of a payout's net
    # was automatically withheld to settle a prior HostRecovery rather than
    # actually reaching the host -- server_default='0' backfills every
    # existing payout row to "no offset applied", the same as this build's
    # prior behavior (which had no such capability at all).
    op.add_column(
        'payout_records', sa.Column('recovery_offset_amount', sa.Numeric(12, 2), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('payout_records', 'recovery_offset_amount')
