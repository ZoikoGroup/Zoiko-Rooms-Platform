"""add holdover policy to market policy pack

Revision ID: 2773b9dff755
Revises: 0a6e6c58504a
Create Date: 2026-09-21 10:41:24.133779

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2773b9dff755'
down_revision: Union[str, None] = '0a6e6c58504a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_policy_packs",
        sa.Column("holdover_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("holdover_rent_multiple", sa.Numeric(6, 2), nullable=False, server_default="1.0"),
    )


def downgrade() -> None:
    op.drop_column("market_policy_packs", "holdover_rent_multiple")
    op.drop_column("market_policy_packs", "holdover_allowed")
