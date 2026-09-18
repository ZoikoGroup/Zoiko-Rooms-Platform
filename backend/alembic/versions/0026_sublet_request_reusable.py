"""drop the overly-strict one-sublet-request-per-occupancy-ever unique
constraint. The app logic already meant to block only concurrent PENDING
requests (see crud/sublet.py:submit_sublet_request's own duplicate check) --
but the DB constraint blocked ANY second request forever, even long after the
first was resolved (approved/rejected). Live testing found this crashes with
a raw 500 (IntegrityError) instead of a clean error, for anyone trying to
submit a second sublet on a room that has ever had one before.

Revision ID: 0026_sublet_request_reusable
Revises: 0025_room_capacity_cotenant
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0026_sublet_request_reusable"
down_revision: Union[str, None] = "0025_room_capacity_cotenant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("sublet_requests_current_occupancy_id_key", "sublet_requests", type_="unique")
    op.create_index("ix_sublet_requests_current_occupancy_id", "sublet_requests", ["current_occupancy_id"])


def downgrade() -> None:
    op.drop_index("ix_sublet_requests_current_occupancy_id", table_name="sublet_requests")
    op.create_unique_constraint("sublet_requests_current_occupancy_id_key", "sublet_requests", ["current_occupancy_id"])
