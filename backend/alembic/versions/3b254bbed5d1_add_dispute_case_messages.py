"""add dispute case messages

ZR-ENG-CLR-010 Phase 11: structured, moderated case-room messaging
(case_message), see app/models/dispute_message.py.

Revision ID: 3b254bbed5d1
Revises: 71d887d62a0c
Create Date: 2026-09-15 00:00:08.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b254bbed5d1'
down_revision: Union[str, None] = '71d887d62a0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispute_case_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_role", sa.String(length=10), nullable=False),
        sa.Column("sender_guest_id", sa.String(), sa.ForeignKey("guests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sender_party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sender_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("body", sa.String(length=4000), nullable=False),
        sa.Column("visibility_class", sa.String(length=20), nullable=False, server_default="PARTY_VISIBLE"),
        sa.Column("moderation_state", sa.String(length=10), nullable=False, server_default="VISIBLE"),
        sa.Column("moderated_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_dispute_case_messages_case_id", "dispute_case_messages", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_case_messages_case_id", table_name="dispute_case_messages")
    op.drop_table("dispute_case_messages")
