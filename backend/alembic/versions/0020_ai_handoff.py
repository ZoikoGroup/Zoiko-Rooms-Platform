"""add ai_handoff table for human handoff subsystem

Revision ID: 0020_ai_handoff
Revises: 71f4a2b9c0aa
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_ai_handoff"
down_revision: Union[str, None] = "71f4a2b9c0aa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_handoffs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("initiator_user_id", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=50), nullable=False),
        sa.Column("urgency", sa.String(length=20), nullable=False, server_default="NORMAL"),
        sa.Column("support_case_ref", sa.String(length=100), nullable=True),
        sa.Column("shared_context_manifest", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("summary", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="REQUESTED"),
        sa.Column("consent_state", sa.String(length=30), nullable=False, server_default="NOTICE_GIVEN"),
        sa.Column("bridge_messages_json", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["chat_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiator_user_id"], ["user_accounts.id"]),
    )
    op.create_index(op.f("ix_ai_handoffs_conversation_id"), "ai_handoffs", ["conversation_id"])
    op.create_index(op.f("ix_ai_handoffs_initiator_user_id"), "ai_handoffs", ["initiator_user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_handoffs_initiator_user_id"), table_name="ai_handoffs")
    op.drop_index(op.f("ix_ai_handoffs_conversation_id"), table_name="ai_handoffs")
    op.drop_table("ai_handoffs")
