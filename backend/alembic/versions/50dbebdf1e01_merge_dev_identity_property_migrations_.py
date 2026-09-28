"""merge dev identity/property migrations with rent and listing fee migrations

Revision ID: 50dbebdf1e01
Revises: d5e9f3a7b2c4, 9a05284cd2f3
Create Date: 2026-09-28 17:05:37.628991

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '50dbebdf1e01'
down_revision: Union[str, None] = ('d5e9f3a7b2c4', '9a05284cd2f3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
