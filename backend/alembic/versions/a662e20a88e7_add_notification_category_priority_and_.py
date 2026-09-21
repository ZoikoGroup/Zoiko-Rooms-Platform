"""add notification category priority and preferences

Revision ID: a662e20a88e7
Revises: 8f2a9b6c1d3e
Create Date: 2026-09-21 14:14:45.821833

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a662e20a88e7'
down_revision: Union[str, None] = '8f2a9b6c1d3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("category", sa.String(length=30), nullable=False, server_default="OCCUPANCY"),
    )
    op.add_column(
        "notifications",
        sa.Column("priority", sa.String(length=10), nullable=False, server_default="NORMAL"),
    )
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient_type", sa.String(length=10), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("recipient_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("recipient_key", sa.String(length=20), nullable=False),
        sa.Column("opted_out_categories", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("quiet_hours_start_minute", sa.Integer(), nullable=False, server_default="1320"),
        sa.Column("quiet_hours_end_minute", sa.Integer(), nullable=False, server_default="420"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("recipient_key", name="uq_notification_preferences_recipient"),
    )


def downgrade() -> None:
    op.drop_table("notification_preferences")
    op.drop_column("notifications", "priority")
    op.drop_column("notifications", "category")
