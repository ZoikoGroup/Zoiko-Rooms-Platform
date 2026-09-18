"""add dispute_resolution_holds.review_at

ZR-ENG-CLR-010 Phase 25: AC-10 "Financial holds are... time/review
bounded" -- see app/models/dispute.py's DisputeResolutionHold.review_at
and app/crud/disputes.py:is_hold_overdue_for_review.

Revision ID: 568dce952e84
Revises: 9bd0428c1fe8
Create Date: 2026-09-15 00:00:21.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '568dce952e84'
down_revision: Union[str, None] = '9bd0428c1fe8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_resolution_holds", sa.Column("review_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("dispute_resolution_holds", "review_at")
