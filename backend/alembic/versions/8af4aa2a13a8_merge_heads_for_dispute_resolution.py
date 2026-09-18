"""merge heads for dispute resolution

Revision ID: 8af4aa2a13a8
Revises: 8bdbaf03c960, 9e1b6c4a8d37
Create Date: 2026-09-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8af4aa2a13a8'
down_revision: Union[str, None] = ('8bdbaf03c960', '9e1b6c4a8d37')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
