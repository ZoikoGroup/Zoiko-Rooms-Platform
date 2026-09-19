"""add reason field to sublet_requests

Revision ID: c7c4d17df125
Revises: 897a4e524d51
Create Date: 2026-09-19 11:36:17.987894

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7c4d17df125'
down_revision: Union[str, None] = '897a4e524d51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("reason", sa.String(length=2000), nullable=False, server_default=""))
    op.alter_column("sublet_requests", "reason", server_default=None)


def downgrade() -> None:
    op.drop_column("sublet_requests", "reason")
