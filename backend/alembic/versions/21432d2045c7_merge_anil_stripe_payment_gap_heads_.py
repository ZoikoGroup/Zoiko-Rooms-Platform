"""merge anil stripe/payment-gap heads with dev

Revision ID: 21432d2045c7
Revises: 412774a1279b, 7d2ca91522b4
Create Date: 2026-09-17 09:13:33.784346

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '21432d2045c7'
down_revision: Union[str, None] = ('412774a1279b', '7d2ca91522b4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
