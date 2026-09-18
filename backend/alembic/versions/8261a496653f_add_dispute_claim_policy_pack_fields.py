"""add dispute claim policy_pack_id/policy_pack_version

ZR-ENG-CLR-010 Phase 22: Section 7 structural, queryable jurisdiction
policy reference on a claim -- previously only embedded in
resolver_notes' free text. See app/services/dispute_forum_resolver.py's
ForumResolution and app/models/dispute.py's DisputeResolutionClaim.

Revision ID: 8261a496653f
Revises: 35b1d803a03c
Create Date: 2026-09-15 00:00:18.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8261a496653f'
down_revision: Union[str, None] = '35b1d803a03c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dispute_resolution_claims",
        sa.Column("policy_pack_id", sa.Integer(), sa.ForeignKey("market_policy_packs.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column("dispute_resolution_claims", sa.Column("policy_pack_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("dispute_resolution_claims", "policy_pack_version")
    op.drop_column("dispute_resolution_claims", "policy_pack_id")
