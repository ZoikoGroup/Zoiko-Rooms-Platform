"""Renter <-> subtenant messaging (sublet_messages)

One thread per sublet request between the renter who asked to sublet and the
person they named -- see app/models/rental_message.py:SubletMessage.

Revision ID: 0055_sublet_messages
Revises: 0054_super_admin_only
Create Date: 2026-10-23 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0055_sublet_messages"
down_revision: Union[str, None] = "0054_super_admin_only"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sublet_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sublet_request_id", sa.Integer(), sa.ForeignKey("sublet_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_role", sa.String(length=10), nullable=False),
        sa.Column("sender_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sublet_messages_sublet_request_id", "sublet_messages", ["sublet_request_id"])


def downgrade() -> None:
    op.drop_index("ix_sublet_messages_sublet_request_id", table_name="sublet_messages")
    op.drop_table("sublet_messages")
