"""add market release policy overrides

Revision ID: af63da49be56
Revises: 7a5b929a868c
Create Date: 2026-09-09 10:37:49.954161

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af63da49be56'
down_revision: Union[str, None] = '7a5b929a868c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'market_releases',
        sa.Column('policy_overrides', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column('market_releases', 'policy_overrides', server_default=None)


def downgrade() -> None:
    op.drop_column('market_releases', 'policy_overrides')
