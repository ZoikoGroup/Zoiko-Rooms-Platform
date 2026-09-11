"""add sublet_requests.payee_model -- ZR-ENG-CLR-003 Rule 4.5's resolved payee,
now sourced from the market policy pack instead of assumed.

Revision ID: 0028_sublet_payee_model
Revises: 0027_market_policy_packs
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028_sublet_payee_model"
down_revision: Union[str, None] = "0027_market_policy_packs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sublet_requests", sa.Column("payee_model", sa.String(length=30), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("sublet_requests", "payee_model")
