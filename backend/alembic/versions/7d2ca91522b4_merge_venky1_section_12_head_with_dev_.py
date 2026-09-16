"""merge venky1 section 12 head with dev dispute management head

Revision ID: 7d2ca91522b4
Revises: 1e5262c64fcc, fb76a9c7eeb7
Create Date: 2026-09-16 09:42:13.280154

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d2ca91522b4'
down_revision: Union[str, None] = ('1e5262c64fcc', 'fb76a9c7eeb7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
