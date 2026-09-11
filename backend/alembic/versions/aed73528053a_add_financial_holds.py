"""add financial_holds

Revision ID: aed73528053a
Revises: bd93a59c37d6
Create Date: 2026-09-10 12:56:57.973861

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aed73528053a'
down_revision: Union[str, None] = 'bd93a59c37d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'financial_holds',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('source_type', sa.String(length=30), nullable=False),
        sa.Column('source_id', sa.String(length=50), nullable=False),
        sa.Column('reason_code', sa.String(length=50), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False, server_default='MEDIUM'),
        sa.Column('description', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='OPEN'),
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by_admin_id', sa.Integer(), sa.ForeignKey('admin_users.id'), nullable=True),
        sa.Column('resolution_notes', sa.String(length=2000), nullable=False, server_default=''),
    )


def downgrade() -> None:
    op.drop_table('financial_holds')
