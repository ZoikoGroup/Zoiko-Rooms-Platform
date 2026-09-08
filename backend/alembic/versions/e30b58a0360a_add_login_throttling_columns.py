"""add login throttling columns

Revision ID: e30b58a0360a
Revises: d3f1a9c2b6e4
Create Date: 2026-09-04 11:52:41.386733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e30b58a0360a'
down_revision: Union[str, None] = 'd3f1a9c2b6e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("admin_users", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("admin_users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_accounts", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("user_accounts", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("user_accounts", "locked_until")
    op.drop_column("user_accounts", "failed_login_attempts")
    op.drop_column("admin_users", "locked_until")
    op.drop_column("admin_users", "failed_login_attempts")
