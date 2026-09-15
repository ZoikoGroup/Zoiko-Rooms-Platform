"""add properties.jurisdiction_code

Revision ID: 8a4d3f01c7e2
Revises: 5c2a7e9f14b6
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a4d3f01c7e2'
down_revision: Union[str, None] = '5c2a7e9f14b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 6: 'The Termination Policy Resolver must select
    # an effective-dated market rule set using the property jurisdiction.'
    # server_default='IN' backfills every existing property to this build's
    # only jurisdiction to date -- a genuine no-op for every property that
    # doesn't explicitly set another one going forward.
    op.add_column('properties', sa.Column('jurisdiction_code', sa.String(10), nullable=False, server_default='IN'))


def downgrade() -> None:
    op.drop_column('properties', 'jurisdiction_code')
