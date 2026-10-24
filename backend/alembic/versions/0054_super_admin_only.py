"""Super admin is the only admin role new accounts get

The limited "admin" role is no longer issued (app/models/admin_user.py
ADMIN_ROLES). Any leftover "admin" row is promoted to super_admin so no
account is left on a role the admin dashboard no longer shows.

Revision ID: 0054_super_admin_only
Revises: 0053_host_authority_hav
Create Date: 2026-10-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "0054_super_admin_only"
down_revision: Union[str, None] = "0053_host_authority_hav"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE admin_users SET role = 'super_admin' WHERE role = 'admin'")
    op.alter_column("admin_users", "role", server_default="super_admin")


def downgrade() -> None:
    # The promotion can't be undone -- which rows were "admin" isn't recorded.
    op.alter_column("admin_users", "role", server_default=None)
