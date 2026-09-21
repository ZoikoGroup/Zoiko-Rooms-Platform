"""merge outstanding heads

Revision ID: 7f656f3fef0b
Revises: 0023_public_assistant, 294dc9f9f823
Create Date: 2026-09-18 06:40:42.755893

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f656f3fef0b'
down_revision: Union[str, None] = ('0023_public_assistant', '294dc9f9f823')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
