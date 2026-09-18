"""add offer accepted_at/confirmation_expires_at (ZR-ENG-CLR-001 Rule 7 -- booking acceptance/expiry scheduler)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('offers', sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('offers', sa.Column('confirmation_expires_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('offers', 'confirmation_expires_at')
    op.drop_column('offers', 'accepted_at')
