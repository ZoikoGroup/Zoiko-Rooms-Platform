"""add termination_cases.evidence_refs

Revision ID: 9d3f6a1c7e52
Revises: 7a1c9e4d2b83
Create Date: 2026-09-12 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d3f6a1c7e52'
down_revision: Union[str, None] = '7a1c9e4d2b83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 21.1/models.termination_case.
    # EVIDENCE_GATED_CAUSE_CODES: opaque evidence references a renter
    # attaches to substantiate a claimed protected/statutory termination
    # ground (RENTER_STATUTORY_RIGHT) -- server_default='[]' backfills every
    # existing case row to "no evidence attached", the same as this build's
    # prior behavior (which had no such field at all).
    op.add_column(
        'termination_cases', sa.Column('evidence_refs', sa.JSON(), nullable=False, server_default='[]'),
    )


def downgrade() -> None:
    op.drop_column('termination_cases', 'evidence_refs')
