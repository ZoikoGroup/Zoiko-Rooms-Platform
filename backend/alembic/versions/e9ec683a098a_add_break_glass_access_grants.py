"""add break glass access grants

Revision ID: e9ec683a098a
Revises: 51fe2deca6a8
Create Date: 2026-09-15 15:01:02.289966

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9ec683a098a'
down_revision: Union[str, None] = '51fe2deca6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "break_glass_access_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("related_entity_type", sa.String(length=50), nullable=False),
        sa.Column("related_entity_id", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_break_glass_access_grants_admin_id", "break_glass_access_grants", ["admin_id"])


def downgrade() -> None:
    op.drop_index("ix_break_glass_access_grants_admin_id", table_name="break_glass_access_grants")
    op.drop_table("break_glass_access_grants")
