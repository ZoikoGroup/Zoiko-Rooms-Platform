"""Let the person who opened a completed dispute remove it from their view

Revision ID: 0057_dispute_removed_by_opener
Revises: 0056_backfill_classification
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0057_dispute_removed_by_opener"
down_revision: Union[str, None] = "0056_backfill_classification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_resolution_cases", sa.Column("removed_by_opener_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("dispute_resolution_cases", "removed_by_opener_at")
