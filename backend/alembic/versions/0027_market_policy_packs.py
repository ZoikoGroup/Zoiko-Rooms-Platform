"""add market_policy_packs -- the jurisdiction/market-pack engine both
ZR-ENG-CLR-002 (Deposit Rules) and ZR-ENG-CLR-003 (Sublet Rules) require:
deposit and sublet code must resolve values from a versioned, effective-dated
policy row instead of hard-coded literals. Seeds one India policy pack,
confidence=REVIEW_REQUIRED (reasonable placeholder values, not verified
legal research -- see model docstring).

Revision ID: 0027_market_policy_packs
Revises: 0026_sublet_request_reusable
Create Date: 2026-09-09 00:00:00.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027_market_policy_packs"
down_revision: Union[str, None] = "0026_sublet_request_reusable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_policy_packs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jurisdiction_code", sa.String(length=10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default="REVIEW_REQUIRED"),
        sa.Column("legal_source_note", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("deposit_instrument_allowed", sa.String(length=20), nullable=False, server_default="OPTIONAL"),
        sa.Column("deposit_max_rent_multiple", sa.Numeric(6, 2), nullable=False, server_default="3.0"),
        sa.Column("deposit_custody_model", sa.String(length=30), nullable=False, server_default="HOST_OR_AGENT"),
        sa.Column("deposit_protection_deadline_days", sa.Integer(), nullable=True),
        sa.Column("deposit_release_deadline_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("sublet_consent_standard", sa.String(length=30), nullable=False, server_default="STATUTORY_RESPONSE_DEADLINE"),
        sa.Column("sublet_consent_response_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("sublet_max_rent_multiple_of_original", sa.Numeric(6, 2), nullable=False, server_default="1.0"),
        sa.Column("sublet_assignment_payee_model", sa.String(length=30), nullable=False, server_default="HOST_OR_LANDLORD_PAYEE"),
        sa.Column("sublet_sublease_payee_model", sa.String(length=30), nullable=False, server_default="ORIGINAL_RENTER_PAYEE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_market_policy_packs_jurisdiction_code", "market_policy_packs", ["jurisdiction_code"])

    policy_packs = sa.table(
        "market_policy_packs",
        sa.column("jurisdiction_code", sa.String),
        sa.column("version", sa.Integer),
        sa.column("effective_from", sa.Date),
        sa.column("confidence", sa.String),
        sa.column("legal_source_note", sa.String),
        sa.column("deposit_instrument_allowed", sa.String),
        sa.column("deposit_max_rent_multiple", sa.Numeric),
        sa.column("deposit_custody_model", sa.String),
        sa.column("deposit_release_deadline_days", sa.Integer),
        sa.column("sublet_consent_standard", sa.String),
        sa.column("sublet_consent_response_days", sa.Integer),
        sa.column("sublet_max_rent_multiple_of_original", sa.Numeric),
        sa.column("sublet_assignment_payee_model", sa.String),
        sa.column("sublet_sublease_payee_model", sa.String),
        sa.column("created_at", sa.DateTime),
    )
    op.bulk_insert(
        policy_packs,
        [
            {
                "jurisdiction_code": "IN",
                "version": 1,
                "effective_from": "2026-01-01",
                "confidence": "REVIEW_REQUIRED",
                "legal_source_note": (
                    "Placeholder MVP values, not verified legal research. India has no single "
                    "national residential tenancy statute -- rules vary by state (Model Tenancy "
                    "Act adoption is state-by-state and most existing tenancies remain under older "
                    "state Rent Control Acts). deposit_max_rent_multiple=3.0 reflects a commonly "
                    "cited market convention (2-3 months), not a statutory cap. Must be reviewed "
                    "against actual state-level law before this governs real money or real tenancies."
                ),
                "deposit_instrument_allowed": "OPTIONAL",
                "deposit_max_rent_multiple": 3.0,
                "deposit_custody_model": "HOST_OR_AGENT",
                "deposit_release_deadline_days": 30,
                "sublet_consent_standard": "STATUTORY_RESPONSE_DEADLINE",
                "sublet_consent_response_days": 14,
                "sublet_max_rent_multiple_of_original": 1.0,
                "sublet_assignment_payee_model": "HOST_OR_LANDLORD_PAYEE",
                "sublet_sublease_payee_model": "ORIGINAL_RENTER_PAYEE",
                "created_at": datetime.now(timezone.utc),
            }
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_market_policy_packs_jurisdiction_code", table_name="market_policy_packs")
    op.drop_table("market_policy_packs")
