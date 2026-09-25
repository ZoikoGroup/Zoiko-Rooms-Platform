"""add ocr address matching columns to property verifications

Revision ID: 7d71d8bff311
Revises: d4733e3d83c6
Create Date: 2026-09-24 15:25:46.685837

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d71d8bff311'
down_revision: Union[str, None] = 'd4733e3d83c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('property_verifications', sa.Column('ocr_extracted_text', sa.String(length=500), nullable=False, server_default=''))
    op.add_column('property_verifications', sa.Column('ocr_confidence', sa.Float(), nullable=True))
    op.add_column('property_verifications', sa.Column('ocr_address_matched', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('property_verifications', 'ocr_address_matched')
    op.drop_column('property_verifications', 'ocr_confidence')
    op.drop_column('property_verifications', 'ocr_extracted_text')
