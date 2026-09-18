"""add termination_cases.policy_snapshot

Revision ID: a5945facff7e
Revises: 36a14b296606
Create Date: 2026-09-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5945facff7e'
down_revision: Union[str, None] = '36a14b296606'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 AC-02/Section 6's own CONTROL: the market-pack snapshot
    # resolved at case-open time, so a case's effective date stays
    # reproducible even after termination_notice_days later changes -- see
    # models/termination_case.py's own field docstring. Existing rows predate
    # this column and get an empty snapshot (their effective date was already
    # computed and stored; only the audit trail behind it is unavailable).
    op.add_column('termination_cases', sa.Column('policy_snapshot', sa.JSON(), nullable=False, server_default='{}'))


def downgrade() -> None:
    op.drop_column('termination_cases', 'policy_snapshot')
