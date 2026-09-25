"""add ocr extracted number and confidence to identity verifications

Revision ID: f6c6202cb9fd
Revises: 1676e9d51d5e
Create Date: 2026-09-23 15:35:30.186024

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6c6202cb9fd'
down_revision: Union[str, None] = '1676e9d51d5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('identity_verifications', sa.Column('ocr_extracted_number', sa.String(length=64), nullable=True))
    op.add_column('identity_verifications', sa.Column('ocr_confidence', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('identity_verifications', 'ocr_confidence')
    op.drop_column('identity_verifications', 'ocr_extracted_number')
