"""add dispute_cases obligation_id, amount, chargeback_outcome

Revision ID: 53b9cafae16a
Revises: 2b8af67b188f
Create Date: 2026-09-10 14:38:53.213740

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53b9cafae16a'
down_revision: Union[str, None] = '2b8af67b188f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('dispute_cases', sa.Column('obligation_id', sa.Integer(), sa.ForeignKey('obligations.id', ondelete='CASCADE'), nullable=True))
    op.add_column('dispute_cases', sa.Column('amount', sa.Numeric(12, 2), nullable=True))
    op.add_column('dispute_cases', sa.Column('chargeback_outcome', sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column('dispute_cases', 'chargeback_outcome')
    op.drop_column('dispute_cases', 'amount')
    op.drop_column('dispute_cases', 'obligation_id')
