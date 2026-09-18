"""add disclosure requirements table

Revision ID: e67c30764cbb
Revises: 792ec39e0d10
Create Date: 2026-09-09 14:16:11.613892

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e67c30764cbb'
down_revision: Union[str, None] = '792ec39e0d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'disclosure_requirements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('agreement_id', sa.Integer(), sa.ForeignKey('agreements.id', ondelete='CASCADE'), nullable=False),
        sa.Column('disclosure_type', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('required', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='REQUIRED_MISSING'),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('disclosure_requirements')
