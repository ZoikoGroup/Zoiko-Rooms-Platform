"""add dispute_decisions.reason_category

ZR-ENG-CLR-010 Phase 28: QA-Q51 "Admin tries to mark legal liability
using a service-level resolution code; blocked by authority matrix." --
see app/models/dispute_decision.py's
DISPUTE_INTERNAL_DECISION_REASON_CATEGORIES, validated by
app/crud/disputes.py:decide_claim.

Revision ID: 3c55e315945c
Revises: 2ffa6f9cba55
Create Date: 2026-09-15 00:00:24.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c55e315945c'
down_revision: Union[str, None] = '2ffa6f9cba55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dispute_decisions",
        sa.Column("reason_category", sa.String(length=30), nullable=False, server_default="OTHER_SERVICE_REASON"),
    )


def downgrade() -> None:
    op.drop_column("dispute_decisions", "reason_category")
