"""add arrangement classification, liability tracking, deposit disposition,
and a stable requester link to sublet_requests (India-scope MVP of
ZR-ENG-CLR-003 Section 3 -- Rental/Sublet Rules)

Revision ID: 0024_sublet_classification
Revises: 0023_deposit_instruments_claims
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024_sublet_classification"
down_revision: Union[str, None] = "0023_deposit_instruments_claims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("arrangement_type", sa.String(length=30), nullable=False, server_default="ASSIGNMENT_FULL"))
    # Nullable: existing rows predate this column and have no reliable original
    # requester on record. New rows always set it at submission time, before the
    # occupancy's guest_id can drift to a replacement occupant.
    op.add_column("sublet_requests", sa.Column("requested_by_guest_id", sa.String(), sa.ForeignKey("guests.id", ondelete="SET NULL"), nullable=True))
    op.add_column("sublet_requests", sa.Column("original_renter_liability", sa.String(length=20), nullable=False, server_default="ACTIVE"))
    op.add_column("sublet_requests", sa.Column("new_occupant_liability", sa.String(length=20), nullable=False, server_default="NONE"))
    op.add_column("sublet_requests", sa.Column("deposit_disposition", sa.String(length=40), nullable=False, server_default=""))
    op.add_column("sublet_requests", sa.Column("policy_snapshot", sa.JSON(), nullable=False, server_default="{}"))
    op.create_index("ix_sublet_requests_requested_by_guest_id", "sublet_requests", ["requested_by_guest_id"])


def downgrade() -> None:
    op.drop_index("ix_sublet_requests_requested_by_guest_id", table_name="sublet_requests")
    op.drop_column("sublet_requests", "policy_snapshot")
    op.drop_column("sublet_requests", "deposit_disposition")
    op.drop_column("sublet_requests", "new_occupant_liability")
    op.drop_column("sublet_requests", "original_renter_liability")
    op.drop_column("sublet_requests", "requested_by_guest_id")
    op.drop_column("sublet_requests", "arrangement_type")
