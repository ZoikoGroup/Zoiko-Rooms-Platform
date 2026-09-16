"""merge anil section 10 dispute evidence head with dev align_termination head

Revision ID: b0bc6ccb5afd
Revises: 1e5262c64fcc, fb76a9c7eeb7
Create Date: 2026-09-16 10:05:20.443056

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0bc6ccb5afd'
down_revision: Union[str, None] = ('1e5262c64fcc', 'fb76a9c7eeb7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
