"""add agreement payment checkout states and session expiry

Revision ID: 0933376a65c2
Revises: bfbe045e1cd5
Create Date: 2026-09-09 09:50:08.072921

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0933376a65c2'
down_revision: Union[str, None] = 'bfbe045e1cd5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('agreements', sa.Column('payment_session_expires_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('agreements', 'payment_session_expires_at')
