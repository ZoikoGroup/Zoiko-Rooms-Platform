"""merge outstanding heads (anil listing-fee, authority relationship type, notification prefs/sublet)

Revision ID: b4ea81bb68d3
Revises: 1f6a4c8e9b2d, 449f80bfb6ad, a1e5489ebb57
Create Date: 2026-09-22 09:50:11.334272

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4ea81bb68d3'
down_revision: Union[str, None] = ('1f6a4c8e9b2d', '449f80bfb6ad', 'a1e5489ebb57')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
