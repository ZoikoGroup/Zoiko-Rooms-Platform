"""add termination liability model fields to market_policy_packs

Revision ID: 7a1c9e4d2b83
Revises: 2be6a6a9dfea
Create Date: 2026-09-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a1c9e4d2b83'
down_revision: Union[str, None] = '2be6a6a9dfea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 11.1/AC-12: which liability model an ordinary
    # renter-initiated early exit/contract break resolves to, plus the
    # rent-multiple formula inputs STATUTORY_BREAK_FEE/CONTRACT_BREAK_AMOUNT/
    # CAPPED_COMPENSATION read (crud/refund_entitlement.py:
    # _compute_policy_liability). server_default='NOTICE_RENT'/'0.0' backfills
    # every existing policy pack row to the exact same "no extra charge
    # beyond rent through the notice period" behavior this build already had
    # -- no behavior change from this migration alone.
    op.add_column(
        'market_policy_packs',
        sa.Column('termination_liability_model', sa.String(30), nullable=False, server_default='NOTICE_RENT'),
    )
    op.add_column(
        'market_policy_packs',
        sa.Column('termination_break_fee_rent_multiple', sa.Numeric(6, 2), nullable=False, server_default='0.0'),
    )
    op.add_column(
        'market_policy_packs',
        sa.Column('termination_liability_cap_rent_multiple', sa.Numeric(6, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('market_policy_packs', 'termination_liability_cap_rent_multiple')
    op.drop_column('market_policy_packs', 'termination_break_fee_rent_multiple')
    op.drop_column('market_policy_packs', 'termination_liability_model')
