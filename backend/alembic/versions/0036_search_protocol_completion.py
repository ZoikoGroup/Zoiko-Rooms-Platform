"""ZR-AI-SEARCH-001 completion: market legal packs, suppression, per-request threads

* external_market_legal_pack -- Section 13 per-market activation gate
  (external search, public fetch, outreach channels, contact release, referral
  fees, sensitive filters, prohibited search terms, sender entity, lead
  protection). Every capability defaults to off.
* provider_suppression -- Sections 9.1/12 opt-out list (hashed contacts).
* external_opportunity_report -- Section 16 stale/inaccurate lead reports.
* external_opportunities.market_code -- the market a lead belongs to; outreach
  and release are gated by that market's pack (null fails closed).
* provider_outreach acceptance capture -- model, terms version, provider name,
  contact-ownership confirmation (Section 9 step 5).
* relay_messages.outreach_id / direct_contact_release.outreach_id -- threads and
  releases are per contact request (one renter + one provider), so the
  one-release-per-opportunity unique index is replaced.

Revision ID: 0036_search_protocol_completion
Revises: 0035_merge_dev_search
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0036_search_protocol_completion"
down_revision: Union[str, None] = "0035_merge_dev_search"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_market_legal_pack",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market_code", sa.String(2), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column("legal_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("privacy_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("commercial_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("approved_by", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_search_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("public_visitor_search_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("public_fetch_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("provider_outreach_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("permitted_outreach_channels", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("direct_contact_release_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("referral_fees_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sensitive_filters_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("prohibited_search_terms", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("lawful_basis_note", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("privacy_notice_url", sa.String(500), nullable=True),
        sa.Column("sender_legal_entity", sa.String(200), nullable=False, server_default=sa.text("'Zoiko Realty Group Inc.'")),
        sa.Column("lead_protection_days", sa.Integer(), nullable=True),
        sa.Column("terms_version", sa.String(40), nullable=False, server_default=sa.text("'1'")),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("market_code", "version", name="uq_emlp_market_version"),
        sa.CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'SUSPENDED')", name="ck_emlp_status"),
    )

    op.create_table(
        "provider_suppression",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("source_id", sa.String(100), nullable=True, index=True),
        sa.Column("reason", sa.String(40), nullable=False, server_default=sa.text("'OPT_OUT'")),
        sa.Column("note", sa.String(500), nullable=False, server_default=sa.text("''")),
        sa.Column("created_by", sa.String(120), nullable=False, server_default=sa.text("'provider'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "external_opportunity_report",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("external_opportunities.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("reporter_user_id", sa.Integer(), sa.ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("reason IN ('STALE', 'INACCURATE', 'SUSPICIOUS', 'OTHER')", name="ck_eor_reason"),
    )

    op.add_column("external_opportunities", sa.Column("market_code", sa.String(2), nullable=True))
    op.create_index("ix_external_opportunities_market_code", "external_opportunities", ["market_code"])

    op.add_column("provider_outreach", sa.Column("acceptance_model", sa.String(40), nullable=True))
    op.add_column("provider_outreach", sa.Column("acceptance_terms_version", sa.String(40), nullable=True))
    op.add_column("provider_outreach", sa.Column("provider_display_name", sa.String(200), nullable=True))
    op.add_column("provider_outreach", sa.Column("provider_contact_confirmed_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column(
        "relay_messages",
        sa.Column("outreach_id", sa.Integer(), sa.ForeignKey("provider_outreach.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_index("ix_relay_messages_outreach_id", "relay_messages", ["outreach_id"])

    # One release per contact request instead of one per opportunity.
    op.drop_index("ix_direct_contact_release_opportunity_id", table_name="direct_contact_release")
    op.create_index("ix_direct_contact_release_opportunity_id", "direct_contact_release", ["opportunity_id"])
    op.add_column(
        "direct_contact_release",
        sa.Column("outreach_id", sa.Integer(), sa.ForeignKey("provider_outreach.id", ondelete="CASCADE"), nullable=True),
    )
    op.create_unique_constraint("uq_dcr_outreach_id", "direct_contact_release", ["outreach_id"])


def downgrade() -> None:
    op.drop_constraint("uq_dcr_outreach_id", "direct_contact_release", type_="unique")
    op.drop_column("direct_contact_release", "outreach_id")
    op.drop_index("ix_direct_contact_release_opportunity_id", table_name="direct_contact_release")
    op.create_index("ix_direct_contact_release_opportunity_id", "direct_contact_release", ["opportunity_id"], unique=True)
    op.drop_index("ix_relay_messages_outreach_id", table_name="relay_messages")
    op.drop_column("relay_messages", "outreach_id")
    for col in ("provider_contact_confirmed_at", "provider_display_name", "acceptance_terms_version", "acceptance_model"):
        op.drop_column("provider_outreach", col)
    op.drop_index("ix_external_opportunities_market_code", table_name="external_opportunities")
    op.drop_column("external_opportunities", "market_code")
    op.drop_table("external_opportunity_report")
    op.drop_table("provider_suppression")
    op.drop_table("external_market_legal_pack")
