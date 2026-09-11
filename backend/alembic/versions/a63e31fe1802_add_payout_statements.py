"""add payout_statements

Revision ID: a63e31fe1802
Revises: 53b9cafae16a
Create Date: 2026-09-10 15:05:37.144475

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a63e31fe1802'
down_revision: Union[str, None] = '53b9cafae16a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payout_statements',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'payout_id', sa.Integer(), sa.ForeignKey('payout_records.id', ondelete='CASCADE'),
            nullable=False, unique=True,
        ),
        sa.Column('statement_number', sa.String(length=30), nullable=False, unique=True),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('storage_ref', sa.String(length=255), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('payout_statements')
