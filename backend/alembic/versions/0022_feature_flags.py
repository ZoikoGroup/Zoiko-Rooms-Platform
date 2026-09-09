"""add feature_flags table (phase 7)

Revision ID: 0022_feature_flags
Revises: 0021_knowledge_base
Create Date: 2026-09-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_feature_flags"
down_revision: Union[str, None] = "0021_knowledge_base"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled_by", sa.String(length=120), nullable=False, server_default="system"),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_feature_flags_name", "feature_flags", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_feature_flags_name", table_name="feature_flags")
    op.drop_table("feature_flags")
