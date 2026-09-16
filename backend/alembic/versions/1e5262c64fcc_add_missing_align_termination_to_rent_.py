"""add missing align_termination_to_rent_cycle column dev never migrated

Revision ID: 1e5262c64fcc
Revises: 3d6195d2873b
Create Date: 2026-09-15 19:15:15.018513

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e5262c64fcc'
down_revision: Union[str, None] = '3d6195d2873b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ZR-ENG-CLR-006 Section 10: dev's ORM model has declared this column
    # since 7a1c9e4d2b83's sibling changes, but no migration on that branch
    # ever actually created it -- a pre-existing gap on dev, not introduced
    # by this merge. server_default=false matches the model's own default
    # and the "off by default" behavior crud/termination.py already assumes.
    op.add_column(
        "market_policy_packs",
        sa.Column("align_termination_to_rent_cycle", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("market_policy_packs", "align_termination_to_rent_cycle")
