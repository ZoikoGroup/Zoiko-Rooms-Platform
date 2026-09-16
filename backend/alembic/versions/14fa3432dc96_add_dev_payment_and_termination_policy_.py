"""add dev payment and termination policy fields to market policy pack

Revision ID: 14fa3432dc96
Revises: 0d55742be987
Create Date: 2026-09-15 18:34:24.879467

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '14fa3432dc96'
down_revision: Union[str, None] = '0d55742be987'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Payment policy (ZR-ENG-CLR-005) -- merged in from the dev branch's
    # independent additions to the same shared MarketPolicyPack table.
    op.add_column(
        "market_policy_packs",
        sa.Column("platform_fee_rate", sa.Numeric(6, 4), nullable=False, server_default="0.10"),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("funds_flow_profile", sa.String(30), nullable=False, server_default="DIRECT_SETTLEMENT"),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("zoiko_legal_entity_name", sa.String(200), nullable=False, server_default="Zoiko Realty Group"),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("zoiko_tax_registration_number", sa.String(50), nullable=False, server_default=""),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("service_fee_tax_rate", sa.Numeric(6, 4), nullable=False, server_default="0.0"),
    )

    # Termination policy (ZR-ENG-CLR-006)
    op.add_column(
        "market_policy_packs",
        sa.Column("termination_notice_days", sa.Integer(), nullable=False, server_default="30"),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("align_termination_to_rent_cycle", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("termination_liability_model", sa.String(30), nullable=False, server_default="NOTICE_RENT"),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("termination_break_fee_rent_multiple", sa.Numeric(6, 2), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "market_policy_packs",
        sa.Column("termination_liability_cap_rent_multiple", sa.Numeric(6, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("market_policy_packs", "termination_liability_cap_rent_multiple")
    op.drop_column("market_policy_packs", "termination_break_fee_rent_multiple")
    op.drop_column("market_policy_packs", "termination_liability_model")
    op.drop_column("market_policy_packs", "align_termination_to_rent_cycle")
    op.drop_column("market_policy_packs", "termination_notice_days")
    op.drop_column("market_policy_packs", "service_fee_tax_rate")
    op.drop_column("market_policy_packs", "zoiko_tax_registration_number")
    op.drop_column("market_policy_packs", "zoiko_legal_entity_name")
    op.drop_column("market_policy_packs", "funds_flow_profile")
    op.drop_column("market_policy_packs", "platform_fee_rate")
