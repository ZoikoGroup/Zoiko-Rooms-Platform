"""add identity required at application to market policy pack

Revision ID: 97bd6e54f43c
Revises: 775705758335
Create Date: 2026-09-15 16:07:31.479962

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '97bd6e54f43c'
down_revision: Union[str, None] = '775705758335'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_policy_packs",
        sa.Column("identity_required_at_application", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("market_policy_packs", "identity_required_at_application")
