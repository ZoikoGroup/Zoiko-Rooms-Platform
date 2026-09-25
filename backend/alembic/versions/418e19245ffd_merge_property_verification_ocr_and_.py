"""merge property verification ocr and listing fee price book heads

Revision ID: 418e19245ffd
Revises: 7d71d8bff311, c3a9f6e2d8b4
Create Date: 2026-09-25 09:29:33.923143

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '418e19245ffd'
down_revision: Union[str, None] = ('7d71d8bff311', 'c3a9f6e2d8b4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
