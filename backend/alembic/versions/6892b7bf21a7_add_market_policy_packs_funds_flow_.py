"""add market_policy_packs.funds_flow_profile

Revision ID: 6892b7bf21a7
Revises: a63e31fe1802
Create Date: 2026-09-10 15:33:19.033261

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6892b7bf21a7'
down_revision: Union[str, None] = 'a63e31fe1802'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-005 AC-20/AC-35: server_default matches this build's only
    # supported profile, so every existing policy pack row backfills to
    # exactly the flow it already implicitly ran as -- no behavior change
    # from this migration alone.
    op.add_column(
        'market_policy_packs', sa.Column('funds_flow_profile', sa.String(30), nullable=False, server_default='DIRECT_SETTLEMENT'),
    )


def downgrade() -> None:
    op.drop_column('market_policy_packs', 'funds_flow_profile')
