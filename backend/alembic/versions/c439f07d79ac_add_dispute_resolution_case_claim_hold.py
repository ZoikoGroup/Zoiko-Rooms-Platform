"""add dispute resolution case, claim, financial hold

ZR-ENG-CLR-010 Phase 1: the general-purpose dispute domain (see
app/models/dispute.py). Separate from the pre-existing dispute_cases/
financial_holds tables, which stay untouched.

Revision ID: c439f07d79ac
Revises: 8af4aa2a13a8
Create Date: 2026-09-15 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c439f07d79ac'
down_revision: Union[str, None] = '8af4aa2a13a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dispute_resolution_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occupancy_id", sa.Integer(), sa.ForeignKey("occupancies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("property_id", sa.Integer(), sa.ForeignKey("properties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("opened_by_guest_id", sa.String(), sa.ForeignKey("guests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("opened_by_party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="SET NULL"), nullable=True),
        sa.Column("severity", sa.String(length=10), nullable=False, server_default="SEV-2"),
        sa.Column("status", sa.String(length=25), nullable=False, server_default="SUBMITTED"),
        sa.Column("primary_claim_family", sa.String(length=30), nullable=False),
        sa.Column("external_dependency_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
    )
    op.create_index("ix_dispute_resolution_cases_occupancy_id", "dispute_resolution_cases", ["occupancy_id"])
    op.create_index("ix_dispute_resolution_cases_property_id", "dispute_resolution_cases", ["property_id"])

    op.create_table(
        "dispute_resolution_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_code", sa.String(length=50), nullable=False),
        sa.Column("claim_family", sa.String(length=30), nullable=False),
        sa.Column("claimant_role", sa.String(length=10), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("requested_remedy", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("authority_class", sa.String(length=2), nullable=True),
        sa.Column("resolver_confidence", sa.String(length=25), nullable=False, server_default="LEGAL_REVIEW_REQUIRED"),
        sa.Column("resolver_notes", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=25), nullable=False, server_default="OPEN"),
        sa.Column("outcome", sa.String(length=30), nullable=True),
        sa.Column("reason_code", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
    )
    op.create_index("ix_dispute_resolution_claims_case_id", "dispute_resolution_claims", ["case_id"])

    op.create_table(
        "dispute_resolution_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("authority_basis", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="PROPOSED"),
        sa.Column("reason_code", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("created_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(length=500), nullable=False, server_default=""),
    )
    op.create_index("ix_dispute_resolution_holds_claim_id", "dispute_resolution_holds", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_dispute_resolution_holds_claim_id", table_name="dispute_resolution_holds")
    op.drop_table("dispute_resolution_holds")
    op.drop_index("ix_dispute_resolution_claims_case_id", table_name="dispute_resolution_claims")
    op.drop_table("dispute_resolution_claims")
    op.drop_index("ix_dispute_resolution_cases_property_id", table_name="dispute_resolution_cases")
    op.drop_index("ix_dispute_resolution_cases_occupancy_id", table_name="dispute_resolution_cases")
    op.drop_table("dispute_resolution_cases")
