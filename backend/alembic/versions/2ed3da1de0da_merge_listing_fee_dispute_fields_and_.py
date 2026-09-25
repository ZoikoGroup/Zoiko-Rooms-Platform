"""merge listing fee dispute fields and property verification ocr heads

Revision ID: 2ed3da1de0da
Revises: 418e19245ffd, d8e4b2c7f1a3
Create Date: 2026-09-25 13:57:27.354251

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2ed3da1de0da'
down_revision: Union[str, None] = ('418e19245ffd', 'd8e4b2c7f1a3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
