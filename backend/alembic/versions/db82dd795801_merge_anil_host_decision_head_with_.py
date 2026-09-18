"""merge anil host-decision head with venky1 payment-method head

Revision ID: db82dd795801
Revises: 78beaf55ea4c, 83df1b6d2ba4
Create Date: 2026-09-17 15:33:21.212966

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'db82dd795801'
down_revision: Union[str, None] = ('78beaf55ea4c', '83df1b6d2ba4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
