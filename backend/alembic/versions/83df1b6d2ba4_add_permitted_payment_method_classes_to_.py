"""add permitted payment method classes to market policy pack

Revision ID: 83df1b6d2ba4
Revises: 21432d2045c7
Create Date: 2026-09-17 11:38:06.085951

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '83df1b6d2ba4'
down_revision: Union[str, None] = '21432d2045c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_policy_packs",
        sa.Column("permitted_payment_method_classes", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("market_policy_packs", "permitted_payment_method_classes")
