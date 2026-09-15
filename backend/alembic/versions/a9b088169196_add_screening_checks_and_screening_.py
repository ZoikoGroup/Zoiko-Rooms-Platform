"""add screening checks and screening prohibited check types

Revision ID: a9b088169196
Revises: 65cf16ab2919
Create Date: 2026-09-15 11:01:57.470108

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9b088169196'
down_revision: Union[str, None] = '65cf16ab2919'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_policy_packs",
        sa.Column("screening_prohibited_check_types", sa.JSON(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "screening_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=50), nullable=False),
        sa.Column("check_type", sa.String(length=50), nullable=False),
        sa.Column("provider_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("permissible_purpose", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("host_policy_criteria", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("provider_result_summary", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("decision_status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("decision_reason", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("adverse_action_notice_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_screening_checks_party_id", "screening_checks", ["party_id"])


def downgrade() -> None:
    op.drop_index("ix_screening_checks_party_id", table_name="screening_checks")
    op.drop_table("screening_checks")
    op.drop_column("market_policy_packs", "screening_prohibited_check_types")
