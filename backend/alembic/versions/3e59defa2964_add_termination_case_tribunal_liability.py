"""add termination_cases.tribunal_liability_amount/reason

Revision ID: 3e59defa2964
Revises: 1efea5419c66
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3e59defa2964'
down_revision: Union[str, None] = '1efea5419c66'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 11.1 TRIBUNAL_OR_COURT_DETERMINED -- a Super
    # Admin's own entry stands in for a real tribunal/court determination,
    # never a computed formula. See models/termination_case.py.
    op.add_column('termination_cases', sa.Column('tribunal_liability_amount', sa.Numeric(12, 2), nullable=False, server_default='0'))
    op.add_column('termination_cases', sa.Column('tribunal_liability_reason', sa.String(length=2000), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('termination_cases', 'tribunal_liability_reason')
    op.drop_column('termination_cases', 'tribunal_liability_amount')
