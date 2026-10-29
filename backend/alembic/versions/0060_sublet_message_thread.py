"""Sublet messages: a host <-> co-tenant thread alongside the renter <-> subtenant one

Revision ID: 0060_sublet_message_thread
Revises: 0059_listing_available_from
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0060_sublet_message_thread"
down_revision: Union[str, None] = "0059_listing_available_from"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Every existing message is a renter <-> subtenant one.
    op.add_column("sublet_messages", sa.Column("thread", sa.String(length=10), nullable=False, server_default="PEER"))


def downgrade() -> None:
    op.drop_column("sublet_messages", "thread")
