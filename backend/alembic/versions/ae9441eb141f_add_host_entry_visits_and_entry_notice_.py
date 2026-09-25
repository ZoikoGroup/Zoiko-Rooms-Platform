"""add host entry visits and entry notice hours

Revision ID: ae9441eb141f
Revises: 77623ea41968
Create Date: 2026-09-21 11:19:49.360388

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ae9441eb141f'
down_revision: Union[str, None] = '77623ea41968'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_policy_packs",
        sa.Column("entry_notice_hours", sa.Integer(), nullable=False, server_default="24"),
    )
    op.create_table(
        "host_entry_visits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occupancy_id", sa.Integer(), sa.ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scheduled_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False, server_default="INSPECTION"),
        sa.Column("notes", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_emergency", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("emergency_reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="SCHEDULED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_host_entry_visits_occupancy_id", "host_entry_visits", ["occupancy_id"])


def downgrade() -> None:
    op.drop_index("ix_host_entry_visits_occupancy_id", table_name="host_entry_visits")
    op.drop_table("host_entry_visits")
    op.drop_column("market_policy_packs", "entry_notice_hours")
