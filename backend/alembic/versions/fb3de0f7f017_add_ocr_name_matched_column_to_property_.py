"""add ocr name matched column to property verifications

Revision ID: fb3de0f7f017
Revises: fced2bd077ac
Create Date: 2026-09-28 13:13:48.799507

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb3de0f7f017'
down_revision: Union[str, None] = 'fced2bd077ac'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("property_verifications", sa.Column("ocr_name_matched", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("property_verifications", "ocr_name_matched")
