"""add occupancy condition report items

Revision ID: 77623ea41968
Revises: 2cbffcb4cfc3
Create Date: 2026-09-21 11:06:44.737650

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '77623ea41968'
down_revision: Union[str, None] = '2cbffcb4cfc3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "occupancy_condition_report_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occupancy_id", sa.Integer(), sa.ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_type", sa.String(length=10), nullable=False),
        sa.Column("area", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("condition_rating", sa.String(length=10), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("stored_filename", sa.String(length=255), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recorded_by_guest_id", sa.String(length=20), sa.ForeignKey("guests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recorded_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_occupancy_condition_report_items_occupancy_id", "occupancy_condition_report_items", ["occupancy_id"])


def downgrade() -> None:
    op.drop_index("ix_occupancy_condition_report_items_occupancy_id", table_name="occupancy_condition_report_items")
    op.drop_table("occupancy_condition_report_items")
