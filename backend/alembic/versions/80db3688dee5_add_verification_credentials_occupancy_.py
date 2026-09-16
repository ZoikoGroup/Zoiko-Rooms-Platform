"""add verification credentials, occupancy eligibility checks, and verification policy fields

Revision ID: 80db3688dee5
Revises: 8bdbaf03c960
Create Date: 2026-09-15 10:12:40.223215

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80db3688dee5'
down_revision: Union[str, None] = '8bdbaf03c960'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("market_policy_packs", sa.Column("occupancy_eligibility_required", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("market_policy_packs", sa.Column("occupancy_eligibility_method_note", sa.String(length=200), nullable=False, server_default=""))
    op.add_column("market_policy_packs", sa.Column("occupancy_eligibility_follow_up_days", sa.Integer(), nullable=True))
    op.add_column("market_policy_packs", sa.Column("identity_evidence_retention_days", sa.Integer(), nullable=False, server_default="90"))

    op.create_table(
        "occupancy_eligibility_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jurisdiction_code", sa.String(length=50), nullable=False),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("share_code", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("evidence_ref", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PENDING"),
        sa.Column("reason_note", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("checked_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("follow_up_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_occupancy_eligibility_checks_party_id", "occupancy_eligibility_checks", ["party_id"])

    op.create_table(
        "verification_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_code", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="VALID"),
        sa.Column("method", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("jurisdiction_code", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("policy_pack_version", sa.Integer(), nullable=True),
        sa.Column("source_identity_verification_id", sa.Integer(), sa.ForeignKey("identity_verifications.id"), nullable=True),
        sa.Column("source_occupancy_eligibility_check_id", sa.Integer(), sa.ForeignKey("occupancy_eligibility_checks.id"), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_verification_credentials_party_id", "verification_credentials", ["party_id"])


def downgrade() -> None:
    op.drop_index("ix_verification_credentials_party_id", table_name="verification_credentials")
    op.drop_table("verification_credentials")
    op.drop_index("ix_occupancy_eligibility_checks_party_id", table_name="occupancy_eligibility_checks")
    op.drop_table("occupancy_eligibility_checks")
    op.drop_column("market_policy_packs", "identity_evidence_retention_days")
    op.drop_column("market_policy_packs", "occupancy_eligibility_follow_up_days")
    op.drop_column("market_policy_packs", "occupancy_eligibility_method_note")
    op.drop_column("market_policy_packs", "occupancy_eligibility_required")
