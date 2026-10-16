"""Payment-detail changes: cooling-off period instead of admin review

A high-risk change to a host's payment instructions now waits
`payment.instruction_cooling_off_hours` (activates_at) and then takes effect
on its own unless the host cancels it. Rows still waiting in the old
PENDING_REVIEW queue join the cooling-off flow (the scheduler gives them a
fresh window).

Revision ID: 0047_instruction_cooling_off
Revises: 0046_habitability_party_flow
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0047_instruction_cooling_off"
down_revision: Union[str, None] = "0046_habitability_party_flow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("rental_payment_instructions", sa.Column("activates_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.execute("UPDATE rental_payment_instructions SET status = 'PENDING_REVIEW' WHERE status = 'COOLING_OFF'")
    op.execute("UPDATE rental_payment_instructions SET status = 'REJECTED' WHERE status = 'CANCELLED'")
    op.drop_column("rental_payment_instructions", "activates_at")
