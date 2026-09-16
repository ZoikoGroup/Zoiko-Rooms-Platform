"""add market_policy_packs dispute deadline/conciliation/waiver fields

ZR-ENG-CLR-010 Phase 29: AC-28/AC-29, QA-Q21/Q22/Q23 -- per-jurisdiction
dispute response/evidence windows, external filing deadline, conciliation
requirement, and non-waivable claim families. See
app/models/market_policy.py's MarketPolicyPack docstring for each field.

Revision ID: feeca8c35d30
Revises: c47cf8fd3652
Create Date: 2026-09-15 00:00:26.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'feeca8c35d30'
down_revision: Union[str, None] = 'c47cf8fd3652'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("market_policy_packs", sa.Column("dispute_response_window_days", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("market_policy_packs", sa.Column("dispute_evidence_window_days", sa.Integer(), nullable=False, server_default="14"))
    op.add_column("market_policy_packs", sa.Column("dispute_external_filing_deadline_days", sa.Integer(), nullable=True))
    op.add_column(
        "market_policy_packs",
        sa.Column("dispute_conciliation_requirement", sa.String(length=20), nullable=False, server_default="NOT_REQUIRED"),
    )
    op.add_column("market_policy_packs", sa.Column("dispute_non_waivable_claim_families", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("market_policy_packs", "dispute_non_waivable_claim_families")
    op.drop_column("market_policy_packs", "dispute_conciliation_requirement")
    op.drop_column("market_policy_packs", "dispute_external_filing_deadline_days")
    op.drop_column("market_policy_packs", "dispute_evidence_window_days")
    op.drop_column("market_policy_packs", "dispute_response_window_days")
