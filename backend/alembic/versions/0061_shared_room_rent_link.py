"""Rent obligations can belong to a shared-room co-tenancy (a co-tenant paying the renter)

Revision ID: 0061_shared_room_rent_link
Revises: 0060_sublet_message_thread
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0061_shared_room_rent_link"
down_revision: Union[str, None] = "0060_sublet_message_thread"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "rental_payment_obligations",
        sa.Column("sublet_request_id", sa.Integer(), sa.ForeignKey("sublet_requests.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_rental_payment_obligations_sublet_request_id", "rental_payment_obligations", ["sublet_request_id"])


def downgrade() -> None:
    op.drop_index("ix_rental_payment_obligations_sublet_request_id", table_name="rental_payment_obligations")
    op.drop_column("rental_payment_obligations", "sublet_request_id")
