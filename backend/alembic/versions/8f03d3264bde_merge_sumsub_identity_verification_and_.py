"""merge sumsub identity verification and rental payment provider governance heads

Revision ID: 8f03d3264bde
Revises: 0db3bd46e0a1, c46f26ba28bd
Create Date: 2026-09-23 11:53:58.720486

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f03d3264bde'
down_revision: Union[str, None] = ('0db3bd46e0a1', 'c46f26ba28bd')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
