"""add dispute forum policy fields to market_policy_packs

ZR-ENG-CLR-010 Phase 13: per-jurisdiction override of
dispute_forum_resolver.py's static claim-family -> authority-class
defaults, see app/models/market_policy.py's own comment. server_defaults
match the resolver's static values exactly, so this migration changes no
resolved behavior for any existing row.

Revision ID: 2d1ad0d02869
Revises: 1e1a3fa9a5bf
Create Date: 2026-09-15 00:00:10.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d1ad0d02869'
down_revision: Union[str, None] = '1e1a3fa9a5bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("market_policy_packs", sa.Column("dispute_deposit_authority_class", sa.String(length=2), nullable=False, server_default="A2"))
    op.add_column("market_policy_packs", sa.Column("dispute_booking_agreement_authority_class", sa.String(length=2), nullable=False, server_default="A1"))
    op.add_column("market_policy_packs", sa.Column("dispute_property_condition_authority_class", sa.String(length=2), nullable=False, server_default="A1"))
    op.add_column("market_policy_packs", sa.Column("dispute_sublet_occupancy_authority_class", sa.String(length=2), nullable=False, server_default="A1"))


def downgrade() -> None:
    op.drop_column("market_policy_packs", "dispute_sublet_occupancy_authority_class")
    op.drop_column("market_policy_packs", "dispute_property_condition_authority_class")
    op.drop_column("market_policy_packs", "dispute_booking_agreement_authority_class")
    op.drop_column("market_policy_packs", "dispute_deposit_authority_class")
