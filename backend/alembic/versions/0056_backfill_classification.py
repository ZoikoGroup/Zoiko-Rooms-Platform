"""Backfill the default occupancy classification for rooms that have none

Rooms created before ensure_default_classification existed have no
occupancy_classifications row, so jurisdiction_gates_pass reports "Occupancy
classification is missing or unresolved" and a host cannot create an agreement
for an accepted offer (Bug ZR-14). New rooms get the default at creation (see
crud/property.py and api/routes/user_hosting.py); this gives the older ones the
same APPROVED default. Rooms that already have a row -- including one an admin
set to UNKNOWN or UNSUPPORTED -- are left untouched.

Revision ID: 0056_backfill_classification
Revises: 0055_sublet_messages
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0056_backfill_classification"
down_revision: Union[str, None] = "0055_sublet_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same values as crud/occupancy_classification.py:ensure_default_classification.
    op.execute(
        """
        INSERT INTO occupancy_classifications
            (room_id, classification, confidence, evidence_ref, jurisdiction, rule_version, review_state, updated_at)
        SELECT r.id, 'shared_residential_room', 1.0, 'auto-default',
               COALESCE(NULLIF(p.jurisdiction_code, ''), party.jurisdiction), 1, 'APPROVED', now()
        FROM rooms r
        JOIN properties p ON p.id = r.property_id
        JOIN parties party ON party.id = p.owner_party_id
        WHERE NOT EXISTS (SELECT 1 FROM occupancy_classifications oc WHERE oc.room_id = r.id)
        """
    )


def downgrade() -> None:
    # Only the rows this migration could have created; an admin-set or
    # creation-time default is indistinguishable, so leave the data in place.
    pass
