"""add external search tables for ZR-AI-SEARCH-001

Creates the core data models for the AI-assisted external search protocol:
* source_right_registry — controlling allow/deny for third-party data sources
* external_opportunities — external room/property leads
* provider_outreach — provider contact attempts
* external_commercial_policy — market-specific billing configuration

Revision ID: 0029_external_search_protocol
Revises: 4d26afb5d759
Create Date: 2026-10-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_external_search_protocol"
down_revision: Union[str, None] = "4d26afb5d759"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- source_right_registry: Section 6.2 ----------------------------------
    op.create_table(
        "source_right_registry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("source_name_internal", sa.String(200), nullable=False),
        sa.Column("territories", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("acquisition_mode", sa.String(30), nullable=False),
        sa.Column("terms_reference", sa.String(500), nullable=True),
        sa.Column("terms_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legal_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("security_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("robots_policy", sa.String(200), nullable=True),
        sa.Column("fetch_rate_limit", sa.Integer(), nullable=True),
        sa.Column("permitted_fields", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("display_permitted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("attribution_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("clickthrough_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("masking_permitted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cache_ttl_seconds", sa.Integer(), nullable=False, server_default=sa.text("3600")),
        sa.Column("contact_extraction_permitted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("outreach_permitted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("outreach_channels", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("source_brand_display_rule", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'BLOCKED'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "ck_srr_acquisition_mode",
        "source_right_registry",
        "acquisition_mode IN ('PARTNER_FEED', 'LICENSED_API', 'PUBLIC_FETCH', 'BLOCKED')",
    )
    op.create_check_constraint(
        "ck_srr_status",
        "source_right_registry",
        "status IN ('ACTIVE', 'REVIEW', 'SUSPENDED', 'BLOCKED')",
    )

    # --- external_opportunities: Sections 3.1, 15.3 --------------------------
    op.create_table(
        "external_opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_opportunity_id", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("source_id", sa.String(100), nullable=False, index=True),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'EXTERNAL_DISCOVERED'")),
        sa.Column("approx_location", sa.String(300), nullable=True),
        sa.Column("advertised_price_currency", sa.String(3), nullable=True),
        sa.Column("advertised_price_minor", sa.Integer(), nullable=True),
        sa.Column("price_period", sa.String(10), nullable=True),
        sa.Column("room_type", sa.String(50), nullable=True),
        sa.Column("permitted_features", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovered_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("provider_name", sa.String(200), nullable=True),
        sa.Column("provider_contact_encrypted", sa.Text(), nullable=True),
        sa.Column("exact_address_encrypted", sa.Text(), nullable=True),
        sa.Column("source_url_encrypted", sa.Text(), nullable=True),
        sa.Column("source_domain", sa.String(200), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=True),
        sa.Column("dedupe_hash", sa.String(64), nullable=True, index=True),
        sa.Column("is_duplicate_of_internal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # listings.id is VARCHAR(20) (String PK, see 0001_initial + Listing model),
        # so the FK column must match -- sa.Integer() here made `alembic upgrade`
        # fail with DatatypeMismatch on the constraint.
        sa.Column("internal_listing_id", sa.String(20), sa.ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("verification_status", sa.String(30), nullable=False, server_default=sa.text("'NOT_VERIFIED_BY_ZOIKO_ROOMS'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "ck_eo_status",
        "external_opportunities",
        "status IN ('EXTERNAL_DISCOVERED', 'OUTREACH_PENDING', 'PROVIDER_ACCEPTED', "
        "'VERIFICATION_IN_PROGRESS', 'INTERNALIZED_VERIFIED', 'BLOCKED')",
    )
    op.create_check_constraint(
        "ck_eo_verification_status",
        "external_opportunities",
        "verification_status IN ('NOT_VERIFIED_BY_ZOIKO_ROOMS', 'VERIFICATION_IN_PROGRESS', "
        "'VERIFIED_IDENTITY', 'VERIFIED_PROPERTY', 'VERIFIED_AUTHORITY')",
    )

    # --- provider_outreach: Section 9 ----------------------------------------
    op.create_table(
        "provider_outreach",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("external_opportunities.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=False, index=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("outreach_status", sa.String(20), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("outreach_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_response", sa.String(20), nullable=True),
        sa.Column("provider_response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_record", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("audit_trail", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "ck_po_outreach_status",
        "provider_outreach",
        "outreach_status IN ('PENDING', 'SENT', 'DELIVERED', 'FAILED', 'SUPPRESSED')",
    )
    op.create_check_constraint(
        "ck_po_channel",
        "provider_outreach",
        "channel IN ('EMAIL', 'SMS', 'PLATFORM_MESSAGE', 'TELEPHONE')",
    )

    # --- external_commercial_policy: Section 10 ------------------------------
    op.create_table(
        "external_commercial_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_code", sa.String(10), nullable=False, index=True),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("provider_type", sa.String(30), nullable=False),
        sa.Column("fee_amount_minor", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("fee_share_basis", sa.String(30), nullable=True),
        sa.Column("trigger_event", sa.String(30), nullable=False),
        sa.Column("legal_approval_ref", sa.String(200), nullable=False),
        sa.Column("tax_rule_ref", sa.String(200), nullable=False),
        sa.Column("billing_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("contract_template_version", sa.String(50), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "kecp_model",
        "external_commercial_policy",
        "model IN ('CLAIM_AND_LIST', 'FIXED_QUALIFIED_INTRODUCTION_FEE', "
        "'FIXED_SUCCESS_FEE', 'AGENCY_SERVICE_FEE_SHARE', 'PARTNER_REVENUE_SHARE')",
    )
    op.create_check_constraint(
        "kecp_provider_type",
        "external_commercial_policy",
        "provider_type IN ('LANDLORD', 'AGENT', 'MANAGER', 'SOURCE_PARTNER')",
    )
    op.create_check_constraint(
        "kecp_trigger_event",
        "external_commercial_policy",
        "trigger_event IN ('PROVIDER_ACCEPTED', 'TENANCY_EXECUTED', 'LISTING_PUBLISHED')",
    )
    op.create_check_constraint(
        "kecp_status",
        "external_commercial_policy",
        "status IN ('DRAFT', 'ACTIVE', 'EXPIRED')",
    )


def downgrade() -> None:
    op.drop_table("provider_outreach")
    op.drop_table("external_opportunities")
    op.drop_table("source_right_registry")
    op.drop_table("external_commercial_policy")