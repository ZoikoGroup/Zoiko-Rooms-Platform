"""Habitability issues settled between host and renter

The host acknowledges and reports a fix; the renter confirms or rejects it
(or is deemed to confirm). A serious issue past its fix date reduces the
next rent automatically. Open incidents get a fix deadline by severity.

Revision ID: 0046_habitability_party_flow
Revises: 0045_host_refunds
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0046_habitability_party_flow"
down_revision: Union[str, None] = "0045_host_refunds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("habitability_incidents", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("habitability_incidents", sa.Column("fix_due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("habitability_incidents", sa.Column("fix_reported_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("habitability_incidents", sa.Column("fix_rejected_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("habitability_incidents", sa.Column("resolution_basis", sa.String(30), nullable=False, server_default=""))
    # Open incidents get the deadline their severity allows, counted from when they opened.
    op.execute("""
        UPDATE habitability_incidents SET fix_due_at = opened_at + CASE severity
            WHEN 'H3' THEN INTERVAL '2 days' WHEN 'H2' THEN INTERVAL '7 days'
            WHEN 'H1' THEN INTERVAL '14 days' ELSE INTERVAL '30 days' END
        WHERE status = 'OPEN'
    """)


def downgrade() -> None:
    op.execute("UPDATE habitability_incidents SET status = 'OPEN' WHERE status IN ('ACKNOWLEDGED', 'FIX_REPORTED')")
    for column in ("resolution_basis", "fix_rejected_count", "fix_reported_at", "fix_due_at", "acknowledged_at"):
        op.drop_column("habitability_incidents", column)
