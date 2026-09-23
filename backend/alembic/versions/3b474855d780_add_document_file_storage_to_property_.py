"""add document file storage to property verifications

Revision ID: 3b474855d780
Revises: ab93adcbfb38
Create Date: 2026-09-23 17:23:47.094212

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b474855d780'
down_revision: Union[str, None] = 'ab93adcbfb38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('property_verifications', sa.Column('document_file_path', sa.String(length=1024), nullable=True))
    op.add_column('property_verifications', sa.Column('document_file_original_name', sa.String(length=255), nullable=False, server_default=''))
    op.add_column('property_verifications', sa.Column('document_file_content_type', sa.String(length=100), nullable=False, server_default=''))
    op.add_column('property_verifications', sa.Column('document_file_size', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('property_verifications', 'document_file_size')
    op.drop_column('property_verifications', 'document_file_content_type')
    op.drop_column('property_verifications', 'document_file_original_name')
    op.drop_column('property_verifications', 'document_file_path')
