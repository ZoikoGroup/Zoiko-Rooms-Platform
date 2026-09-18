"""add listing paused_at and suspension_reason

Revision ID: bfbe045e1cd5
Revises: 4f187c405ebe
Create Date: 2026-09-09 09:24:33.596656

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bfbe045e1cd5'
down_revision: Union[str, None] = '4f187c405ebe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('listings', sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('listings', sa.Column('suspension_reason', sa.String(length=1000), nullable=False, server_default=''))
    op.alter_column('listings', 'suspension_reason', server_default=None)


def downgrade() -> None:
    op.drop_column('listings', 'suspension_reason')
    op.drop_column('listings', 'paused_at')
