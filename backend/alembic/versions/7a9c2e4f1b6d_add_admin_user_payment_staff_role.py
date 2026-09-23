"""add admin_users payment_staff_role

ZR-PAY-LINK-003 Section 17 Permissions Matrix's "Staff" tier, see
app/models/admin_user.py's PAYMENT_STAFF_ROLES and
app/api/deps.py:require_super_admin_or_payment_staff.

Revision ID: 7a9c2e4f1b6d
Revises: c46f26ba28bd
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a9c2e4f1b6d'
down_revision: Union[str, None] = 'c46f26ba28bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("admin_users", sa.Column("payment_staff_role", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_users", "payment_staff_role")
