"""add dispute resolution hold version column

ZR-ENG-CLR-010 Phase 14: AC-39/Q42 optimistic concurrency on
dispute_resolution_holds via SQLAlchemy's version_id_col, see
app/models/dispute.py's DisputeResolutionHold docstring.

Revision ID: b6833bf3ba62
Revises: 2d1ad0d02869
Create Date: 2026-09-15 00:00:11.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6833bf3ba62'
down_revision: Union[str, None] = '2d1ad0d02869'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_resolution_holds", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("dispute_resolution_holds", "version")
