"""add admin_users dispute_role

ZR-ENG-CLR-010 Phase 21: Section 12/20 dispute-specialist RBAC
(Support/Dispute Officer/Finance/Trust & Safety/Legal-Compliance), see
app/models/admin_user.py's DISPUTE_ADMIN_ROLES and
app/services/dispute_rbac.py.

Revision ID: 35b1d803a03c
Revises: e40283c33b63
Create Date: 2026-09-15 00:00:17.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '35b1d803a03c'
down_revision: Union[str, None] = 'e40283c33b63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("admin_users", sa.Column("dispute_role", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("admin_users", "dispute_role")
