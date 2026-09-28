"""add extracted name column to identity verifications

Revision ID: 9a05284cd2f3
Revises: fb3de0f7f017
Create Date: 2026-09-28 14:50:27.053012

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a05284cd2f3'
down_revision: Union[str, None] = 'fb3de0f7f017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("identity_verifications", sa.Column("extracted_name", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("identity_verifications", "extracted_name")
