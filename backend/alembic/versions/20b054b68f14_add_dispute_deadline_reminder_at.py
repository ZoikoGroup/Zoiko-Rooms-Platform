"""add dispute_deadlines.reminder_at

ZR-ENG-CLR-010 Phase 31: AC-29 "Deadlines are computed from the relevant
forum pack, with reminders and extension audit." See
app/crud/dispute_deadline.py's compute_reminder_at/is_reminder_due and
app/crud/disputes.py's new _create_party_response_deadline (auto-created
PARTY_RESPONSE deadline computed from MarketPolicyPack.dispute_response_window_days).

Revision ID: 20b054b68f14
Revises: 9afdc221522c
Create Date: 2026-09-15 00:00:28.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20b054b68f14'
down_revision: Union[str, None] = '9afdc221522c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_deadlines", sa.Column("reminder_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("dispute_deadlines", "reminder_at")
