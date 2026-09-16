"""merge venky1 section 12 head with dev section 5 6 head

Revision ID: 3d6195d2873b
Revises: 0d55742be987, 9e1b6c4a8d37
Create Date: 2026-09-15 19:11:37.609110

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d6195d2873b'
down_revision: Union[str, None] = ('0d55742be987', '9e1b6c4a8d37')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
