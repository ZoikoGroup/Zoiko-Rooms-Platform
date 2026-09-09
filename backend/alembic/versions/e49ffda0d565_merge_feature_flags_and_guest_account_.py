"""merge feature_flags and guest_account_link heads

Revision ID: e49ffda0d565
Revises: 0022_feature_flags, d3f1a9c2b6e4
Create Date: 2026-09-03 14:33:20.178072

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e49ffda0d565'
down_revision: Union[str, None] = ('0022_feature_flags', 'd3f1a9c2b6e4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
