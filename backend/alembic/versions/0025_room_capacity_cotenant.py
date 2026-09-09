"""add rooms.max_occupants and enforce room-capacity at move-in -- the real
prerequisite for true co-tenancy/partial-sublet support (ZR-ENG-CLR-003
Section 3). Also closes a pre-existing gap: nothing previously stopped two
independent tenancies moving into the same room by accident.

Revision ID: 0025_room_capacity_cotenant
Revises: 0024_sublet_classification
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025_room_capacity_cotenant"
down_revision: Union[str, None] = "0024_sublet_classification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Default 1 matches every room's real-world behavior up to today -- this is
    # additive, not a behavior change, until a room's value is explicitly raised.
    op.add_column("rooms", sa.Column("max_occupants", sa.Integer(), nullable=False, server_default="1"))
    # For SUBLEASE_PARTIAL/ADD_CO_TENANT, approval creates a brand new signed
    # Agreement (own rent + deposit obligations) for the co-tenant rather than
    # overwriting the existing occupancy. This is what the co-tenant then pays
    # against and moves in on -- exactly like any other agreement -- so it's the
    # field that's actually useful to expose, not an occupancy that doesn't
    # exist until move-in.
    op.add_column("sublet_requests", sa.Column("new_agreement_id", sa.Integer(), sa.ForeignKey("agreements.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    op.drop_column("sublet_requests", "new_agreement_id")
    op.drop_column("rooms", "max_occupants")
