"""add listing default offer terms

Revision ID: 27c38b20381c
Revises: 4d44645d3fea
Create Date: 2026-09-22 14:58:06.421654

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '27c38b20381c'
down_revision: Union[str, None] = '4d44645d3fea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('default_monthly_rent', sa.Float(), nullable=True))
    op.add_column('listings', sa.Column('default_deposit_amount', sa.Float(), nullable=True))
    op.add_column('listings', sa.Column('default_term_months', sa.Integer(), nullable=True))
    op.add_column('listings', sa.Column('default_cadence', sa.String(length=20), nullable=False, server_default='MONTHLY'))


def downgrade() -> None:
    op.drop_column('listings', 'default_cadence')
    op.drop_column('listings', 'default_term_months')
    op.drop_column('listings', 'default_deposit_amount')
    op.drop_column('listings', 'default_monthly_rent')
