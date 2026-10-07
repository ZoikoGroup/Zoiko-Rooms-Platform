"""authority review case assignment

Revision ID: c7e2a91d4f30
Revises: 4a26522998f0
Create Date: 2026-10-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e2a91d4f30'
down_revision: Union[str, None] = '4a26522998f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """ZR-AUTHORITY-002 Section 13: least-privilege reviewer access with case
    assignment -- only the assigned reviewer opens evidence or decides."""
    op.add_column("authority_verifications", sa.Column("assigned_admin_id", sa.Integer(), nullable=True))
    op.add_column("authority_verifications", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_authority_verifications_assigned_admin", "authority_verifications", "admin_users",
                          ["assigned_admin_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_authority_verifications_assigned_admin", "authority_verifications", type_="foreignkey")
    op.drop_column("authority_verifications", "assigned_at")
    op.drop_column("authority_verifications", "assigned_admin_id")
