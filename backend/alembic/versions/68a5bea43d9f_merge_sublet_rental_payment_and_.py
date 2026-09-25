"""merge sublet, rental payment, and property verification migration heads

Revision ID: 68a5bea43d9f
Revises: 1f6a4c8e9b2d, 449f80bfb6ad, a1e5489ebb57
Create Date: 2026-09-22 09:25:30.806010

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '68a5bea43d9f'
down_revision: Union[str, None] = ('1f6a4c8e9b2d', '449f80bfb6ad', 'a1e5489ebb57')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
