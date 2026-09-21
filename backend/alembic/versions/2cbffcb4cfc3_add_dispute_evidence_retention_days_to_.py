"""add dispute evidence retention days to market policy pack

Revision ID: 2cbffcb4cfc3
Revises: 2773b9dff755
Create Date: 2026-09-21 10:55:07.154390

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2cbffcb4cfc3'
down_revision: Union[str, None] = '2773b9dff755'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_policy_packs",
        sa.Column("dispute_evidence_retention_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("market_policy_packs", "dispute_evidence_retention_days")
