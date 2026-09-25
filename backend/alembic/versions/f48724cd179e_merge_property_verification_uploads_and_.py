"""merge property verification uploads and rental payment bank fields heads

Revision ID: f48724cd179e
Revises: 3b474855d780, c1d8e5f3a7b9
Create Date: 2026-09-24 09:09:13.838798

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f48724cd179e'
down_revision: Union[str, None] = ('3b474855d780', 'c1d8e5f3a7b9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
