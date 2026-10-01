"""email messages: delivery record and dedupe for every email (ZR-COMMS-EMAIL-001 1.3)

Revision ID: f2b5d8e1a4c7
Revises: e6a1c9d3b7f4
Create Date: 2026-10-01 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b5d8e1a4c7'
down_revision: Union[str, None] = 'e6a1c9d3b7f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=40), nullable=False),
        sa.Column("template_version", sa.String(length=20), nullable=False),
        sa.Column("variant", sa.String(length=60), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("stream", sa.String(length=20), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("duplicate_of_id", sa.Integer(), nullable=True),
        sa.Column("related_entity_type", sa.String(length=50), nullable=False),
        sa.Column("related_entity_id", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["email_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_email_messages_template_id", "email_messages", ["template_id"])
    op.create_index("ix_email_messages_recipient_email", "email_messages", ["recipient_email"])


def downgrade() -> None:
    op.drop_index("ix_email_messages_recipient_email", table_name="email_messages")
    op.drop_index("ix_email_messages_template_id", table_name="email_messages")
    op.drop_table("email_messages")
