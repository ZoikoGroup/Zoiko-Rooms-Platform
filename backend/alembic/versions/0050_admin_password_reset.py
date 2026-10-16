"""Admin password reset

password_reset_tokens can belong to an admin (admin_user_id; user_id becomes
nullable), and admin_users.password_changed_at ends older admin sessions
after a reset.

Revision ID: 0050_admin_password_reset
Revises: 0049_deposit_party_flow
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0050_admin_password_reset"
down_revision: Union[str, None] = "0049_deposit_party_flow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("password_reset_tokens") as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "password_reset_tokens",
        sa.Column("admin_user_id", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_password_reset_tokens_admin_user_id", "password_reset_tokens", ["admin_user_id"])
    op.add_column("admin_users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_users", "password_changed_at")
    op.drop_index("ix_password_reset_tokens_admin_user_id", table_name="password_reset_tokens")
    op.execute("DELETE FROM password_reset_tokens WHERE user_id IS NULL")
    op.drop_column("password_reset_tokens", "admin_user_id")
    with op.batch_alter_table("password_reset_tokens") as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
