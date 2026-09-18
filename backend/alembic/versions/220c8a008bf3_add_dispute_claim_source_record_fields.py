"""add dispute claim source_record_type/source_record_id

ZR-ENG-CLR-010 Phase 23: AC-35/36/37 claim-to-source-record linkage --
a PROPERTY_CONDITION/BOOKING_AGREEMENT/SUBLET_OCCUPANCY claim auto-links
to the most relevant existing HabitabilityIncident/BookingChangeRequest/
SubletRequest for its occupancy. See app/crud/disputes.py's
_resolve_source_record and app/models/dispute.py's
DISPUTE_CLAIM_SOURCE_RECORD_TYPES.

Revision ID: 220c8a008bf3
Revises: 8261a496653f
Create Date: 2026-09-15 00:00:19.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '220c8a008bf3'
down_revision: Union[str, None] = '8261a496653f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dispute_resolution_claims", sa.Column("source_record_type", sa.String(length=30), nullable=True))
    op.add_column("dispute_resolution_claims", sa.Column("source_record_id", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("dispute_resolution_claims", "source_record_id")
    op.drop_column("dispute_resolution_claims", "source_record_type")
