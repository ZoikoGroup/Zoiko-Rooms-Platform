"""add landmark column to properties

Revision ID: 8e060940f50e
Revises: 2ed3da1de0da
Create Date: 2026-09-28 12:22:36.090173

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8e060940f50e'
down_revision: Union[str, None] = '2ed3da1de0da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("landmark", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "landmark")
