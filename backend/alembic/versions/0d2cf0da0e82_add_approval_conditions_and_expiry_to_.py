"""add approval conditions and expiry to sublet_requests

Revision ID: 0d2cf0da0e82
Revises: 4e3c72779101
Create Date: 2026-09-19 10:48:39.771108

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0d2cf0da0e82'
down_revision: Union[str, None] = '4e3c72779101'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("approval_conditions", sa.String(length=2000), nullable=False, server_default=""))
    op.add_column("sublet_requests", sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("sublet_requests", "approval_conditions", server_default=None)


def downgrade() -> None:
    op.drop_column("sublet_requests", "approval_expires_at")
    op.drop_column("sublet_requests", "approval_conditions")
