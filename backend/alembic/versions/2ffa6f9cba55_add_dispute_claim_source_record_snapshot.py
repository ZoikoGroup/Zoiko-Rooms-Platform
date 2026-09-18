"""add dispute_resolution_claims.source_record_snapshot

ZR-ENG-CLR-010 Phase 27: AC-35/37/38 -- freezes the specific fields each
AC names (sublet arrangement/consent, booking-change proposal hash,
occupancy handover evidence) from whichever source record a claim links
to, rather than leaving them reachable only via a live join. See
app/crud/disputes.py:_build_source_record_snapshot and
app/models/dispute.py's DisputeResolutionClaim.source_record_snapshot.

Revision ID: 2ffa6f9cba55
Revises: 24b100f1591e
Create Date: 2026-09-15 00:00:23.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2ffa6f9cba55'
down_revision: Union[str, None] = '24b100f1591e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dispute_resolution_claims",
        sa.Column("source_record_snapshot", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("dispute_resolution_claims", "source_record_snapshot")
