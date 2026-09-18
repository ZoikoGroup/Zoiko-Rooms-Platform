"""add deposit_instruments, deposit_claims, deposit_claim_items (India-scope MVP
of Section 2 -- Deposit Rules: canonical instrument type, itemized claims with
evidence, and renter accept/dispute per line item, replacing blind forfeiture)

Revision ID: 0023_deposit_instruments_claims
Revises: 4ce282ecddbb
Create Date: 2026-09-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_deposit_instruments_claims"
down_revision: Union[str, None] = "4ce282ecddbb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deposit_instruments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deposit_record_id", sa.Integer(), sa.ForeignKey("deposit_records.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("instrument_type", sa.String(length=30), nullable=False, server_default="SECURITY_DEPOSIT"),
        sa.Column("custody_model", sa.String(length=30), nullable=False, server_default="HOST_OR_AGENT"),
        sa.Column("calculation_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "deposit_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("deposit_record_id", sa.Integer(), sa.ForeignKey("deposit_records.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="RENTER_RESPONSE_PENDING"),
        sa.Column("submitted_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renter_responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.String(length=2000), nullable=False, server_default=""),
    )

    op.create_table(
        "deposit_claim_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("deposit_claims.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("category_code", sa.String(length=50), nullable=False),
        sa.Column("amount_requested", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("evidence_filename", sa.String(length=255), nullable=True),
        sa.Column("evidence_original_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("evidence_content_type", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("tenant_response", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("final_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("deposit_claim_items")
    op.drop_table("deposit_claims")
    op.drop_table("deposit_instruments")
