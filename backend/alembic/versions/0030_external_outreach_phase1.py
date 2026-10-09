"""Phase 1 outreach workflow columns for ZR-AI-SEARCH-001

Adds the state-machine markers the provider-outreach service (Section 9)
needs after the initial Phase 0 tables:
* external_opportunities.intro_unlocked_at — when a provider-accepted
  opportunity had its controlled introduction unlocked (Step 6)
* provider_outreach outreach_status gains EXPIRED — used by the background
  worker to expire SENT outreach with no response within its TTL

Revision ID: 0030_external_outreach_phase1
Revises: 0029_external_search_protocol
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_external_outreach_phase1"
down_revision: Union[str, None] = "0029_external_search_protocol"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PROVIDER_OUTREACH_STATUSES = (
    "'PENDING', 'SENT', 'DELIVERED', 'FAILED', 'SUPPRESSED', 'EXPIRED'"
)


def upgrade() -> None:
    op.add_column(
        "external_opportunities",
        sa.Column("intro_unlocked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.drop_constraint("ck_po_outreach_status", "provider_outreach", type_="check")
    op.create_check_constraint(
        "ck_po_outreach_status",
        "provider_outreach",
        f"outreach_status IN ({PROVIDER_OUTREACH_STATUSES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_po_outreach_status", "provider_outreach", type_="check")
    op.create_check_constraint(
        "ck_po_outreach_status",
        "provider_outreach",
        "outreach_status IN ('PENDING', 'SENT', 'DELIVERED', 'FAILED', 'SUPPRESSED')",
    )

    op.drop_column("external_opportunities", "intro_unlocked_at")