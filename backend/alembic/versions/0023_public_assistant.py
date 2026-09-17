"""add public_rate_limits table for anonymous public assistant limiting

Revision ID: 0023_public_assistant
Revises: 4ce282ecddbb
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_public_assistant"
down_revision: Union[str, Sequence[str], None] = "4ce282ecddbb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "public_rate_limits",
        sa.Column("bucket_key", sa.String(length=128), nullable=False),
        sa.Column("window_start", sa.BigInteger(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bucket_key"),
    )
    op.create_index(op.f("ix_public_rate_limits_window_start"), "public_rate_limits", ["window_start"])


def downgrade() -> None:
    op.drop_index(op.f("ix_public_rate_limits_window_start"), table_name="public_rate_limits")
    op.drop_table("public_rate_limits")