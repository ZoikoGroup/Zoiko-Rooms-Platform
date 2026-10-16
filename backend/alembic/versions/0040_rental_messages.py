"""Host <-> renter messaging: rental_conversations and rental_messages

One conversation per (application, renter guest), created on the first
message -- so a subtenant who took the tenancy over gets their own thread with
the host, separate from the original renter's. Each message records who sent
it (RENTER/HOST) and when the other side read it.

Revision ID: 0040_rental_messages
Revises: 0039_recipient_reviewed
Create Date: 2026-10-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0040_rental_messages"
down_revision: Union[str, None] = "0039_recipient_reviewed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rental_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("guest_id", sa.String(20), sa.ForeignKey("guests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("application_id", "guest_id", name="uq_rental_conversations_application_guest"),
    )
    op.create_table(
        "rental_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("rental_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_role", sa.String(10), nullable=False),
        sa.Column("sender_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rental_messages_conversation_id", "rental_messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_rental_messages_conversation_id", table_name="rental_messages")
    op.drop_table("rental_messages")
    op.drop_table("rental_conversations")
