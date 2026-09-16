"""add dispute case partial_closure_reason

ZR-ENG-CLR-010 Phase 18/QA-Q45: partial case closure while one or more
claims are genuinely stuck awaiting an open external proceeding, see
app/crud/disputes.py:close_case's force_close_reason path.

Revision ID: 9cf005193d5b
Revises: a0b4588635c2
Create Date: 2026-09-15 00:00:14.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9cf005193d5b'
down_revision: Union[str, None] = 'a0b4588635c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_resolution_cases", sa.Column("partial_closure_reason", sa.String(length=1000), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("dispute_resolution_cases", "partial_closure_reason")
