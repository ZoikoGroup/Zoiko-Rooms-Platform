"""Host sublet policy settings (minimum notice before a sublet starts)

Revision ID: 0058_host_sublet_policy
Revises: 0057_dispute_removed_by_opener
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0058_host_sublet_policy"
down_revision: Union[str, None] = "0057_dispute_removed_by_opener"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "host_sublet_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("min_notice_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("min_notice_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("host_sublet_policies")
