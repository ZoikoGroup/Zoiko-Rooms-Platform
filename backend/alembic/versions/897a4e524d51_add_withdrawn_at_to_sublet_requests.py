"""add withdrawn_at to sublet_requests

Revision ID: 897a4e524d51
Revises: 0d2cf0da0e82
Create Date: 2026-09-19 10:55:31.370847

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '897a4e524d51'
down_revision: Union[str, None] = '0d2cf0da0e82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sublet_requests", "withdrawn_at")
