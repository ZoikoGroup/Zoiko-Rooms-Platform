"""rename booking change request statuses to full state machine vocabulary

Revision ID: e09dabbc0bd4
Revises: 4bc310467149
Create Date: 2026-09-11 10:39:46.143678

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e09dabbc0bd4'
down_revision: Union[str, None] = '4bc310467149'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # See app/services/booking_change_state_machine.py: PENDING/APPROVED/
    # DECLINED collapsed two doc-distinct moments into one overloaded
    # APPROVED and had no terminal state for a failed approval attempt.
    op.alter_column("booking_change_requests", "status", type_=sa.String(length=25))
    op.execute("UPDATE booking_change_requests SET status = 'AWAITING_HOST' WHERE status = 'PENDING'")
    op.execute("UPDATE booking_change_requests SET status = 'AWAITING_AGREEMENT_ACTION' WHERE status = 'APPROVED'")
    op.execute("UPDATE booking_change_requests SET status = 'REJECTED' WHERE status = 'DECLINED'")


def downgrade() -> None:
    op.execute("UPDATE booking_change_requests SET status = 'PENDING' WHERE status = 'AWAITING_HOST'")
    op.execute("UPDATE booking_change_requests SET status = 'APPROVED' WHERE status = 'AWAITING_AGREEMENT_ACTION'")
    op.execute("UPDATE booking_change_requests SET status = 'DECLINED' WHERE status = 'REJECTED'")
    op.execute("UPDATE booking_change_requests SET status = 'DECLINED' WHERE status IN ('CONFLICT', 'FAILED')")
    op.alter_column("booking_change_requests", "status", type_=sa.String(length=20))
