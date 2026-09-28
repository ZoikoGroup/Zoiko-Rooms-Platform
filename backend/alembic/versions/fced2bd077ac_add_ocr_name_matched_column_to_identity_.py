"""add ocr name matched column to identity verifications

Revision ID: fced2bd077ac
Revises: 8e060940f50e
Create Date: 2026-09-28 13:08:55.429051

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fced2bd077ac'
down_revision: Union[str, None] = '8e060940f50e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("identity_verifications", sa.Column("ocr_name_matched", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("identity_verifications", "ocr_name_matched")
