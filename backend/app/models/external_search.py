"""External search models for the Zoiko Rooms AI Search Protocol (ZR-AI-SEARCH-001).

Models
------
* SourceRightRegistry — controlling allow/deny for each third-party data source
* ExternalOpportunity — an external room/property lead discovered via web/API/feed
* ProviderOutreach — record of contact initiated with an external provider
* ExternalCommercialPolicy — market-specific billing/commercial configuration
* ExternalMarketLegalPack — per-market activation gate (Section 13)
* ProviderSuppression — provider opt-out / suppression list (Sections 9.1, 12)
* ExternalOpportunityReport — renter reports of stale/inaccurate leads (Section 16)
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class SourceRightRegistry(Base):
    """Controlling registry of approved external data sources (Section 6.2 of ZR-AI-SEARCH-001).

    Every source must have an explicit entry; no source defaults to 'allowed'.
    """

    __tablename__ = "source_right_registry"
    __table_args__ = (
        CheckConstraint(
            "acquisition_mode IN ('PARTNER_FEED', 'LICENSED_API', 'PUBLIC_FETCH', 'BLOCKED')",
            name="ck_srr_acquisition_mode",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'REVIEW', 'SUSPENDED', 'BLOCKED')",
            name="ck_srr_status",
        ),
        CheckConstraint(
            "feed_format IS NULL OR feed_format IN ('JSON', 'CSV', 'BLM', 'RESO')",
            name="ck_srr_feed_format",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    source_name_internal: Mapped[str] = mapped_column(String(200), nullable=False)
    territories: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    acquisition_mode: Mapped[str] = mapped_column(String(30), nullable=False)  # PARTNER_FEED | LICENSED_API | PUBLIC_FETCH | BLOCKED
    terms_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    terms_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    legal_approved: Mapped[bool] = mapped_column(nullable=False, default=False)
    security_approved: Mapped[bool] = mapped_column(nullable=False, default=False)
    robots_policy: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fetch_rate_limit: Mapped[int | None] = mapped_column(nullable=True)  # requests per minute
    permitted_fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    display_permitted: Mapped[bool] = mapped_column(nullable=False, default=False)
    attribution_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    clickthrough_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    masking_permitted: Mapped[bool] = mapped_column(nullable=False, default=False)
    cache_ttl_seconds: Mapped[int] = mapped_column(nullable=False, default=3600)
    contact_extraction_permitted: Mapped[bool] = mapped_column(nullable=False, default=False)
    outreach_permitted: Mapped[bool] = mapped_column(nullable=False, default=False)
    outreach_channels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_brand_display_rule: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # PARTNER_FEED pull settings (services/feed_sync.py). The credential is
    # never stored here: feed_credential_env names the environment variable
    # that holds the partner's token.
    feed_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    feed_format: Mapped[str | None] = mapped_column(String(10), nullable=True)  # JSON | CSV | BLM | RESO
    feed_credential_env: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # PUBLIC_FETCH website sources: the approved site (e.g. "agency.example.co.uk")
    # that web search results may come from (services/external_providers.py).
    site_domain: Mapped[str | None] = mapped_column(String(253), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="BLOCKED")  # ACTIVE | REVIEW | SUSPENDED | BLOCKED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ExternalOpportunity(Base):
    """An external room/property lead found through an approved source (Section 3.1, 15.3).

    Records progress through the canonical state machine from EXTERNAL_DISCOVERED
    through to INTERNALIZED_VERIFIED or BLOCKED.
    """

    __tablename__ = "external_opportunities"
    __table_args__ = (
        CheckConstraint(
            "status IN ('EXTERNAL_DISCOVERED', 'OUTREACH_PENDING', 'PROVIDER_ACCEPTED', "
            "'VERIFICATION_IN_PROGRESS', 'INTERNALIZED_VERIFIED', 'BLOCKED')",
            name="ck_eo_status",
        ),
        CheckConstraint(
            "verification_status IN ('NOT_VERIFIED_BY_ZOIKO_ROOMS', 'VERIFICATION_IN_PROGRESS', "
            "'VERIFIED_IDENTITY', 'VERIFIED_PROPERTY', 'VERIFIED_AUTHORITY')",
            name="ck_eo_verification_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_opportunity_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # Market (ISO alpha-2) the lead was discovered for; outreach and release are
    # gated by that market's Legal Pack. Null (legacy) fails closed.
    market_code: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="EXTERNAL_DISCOVERED")
    approx_location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    advertised_price_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    advertised_price_minor: Mapped[int | None] = mapped_column(nullable=True)
    price_period: Mapped[str | None] = mapped_column(String(10), nullable=True)  # MONTH | WEEK | NIGHT
    room_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    permitted_features: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    discovered_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_contact_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    exact_address_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_domain: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dedupe_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_duplicate_of_internal: Mapped[bool] = mapped_column(nullable=False, default=False)
    internal_listing_id: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("listings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    verification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NOT_VERIFIED_BY_ZOIKO_ROOMS"
    )
    # ZR-AI-SEARCH-001 Section 11.1: payment-receipt authority is a SEPARATE
    # verification domain from identity/property/authority. It is never implied
    # by provider acceptance or VERIFIED_AUTHORITY; payment instructions only
    # unlock once this flag is set (see CommercialPolicyService.external_payment_eligible).
    payment_receipt_authority_verified: Mapped[bool] = mapped_column(nullable=False, default=False)
    payment_receipt_authority_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ZR-AI-SEARCH-001 Section 11.1: where a sublet is involved, evidence of the
    # required landlord/agent permission. sublet_permission_verified is set only
    # after that evidence is captured; internalization of a sublet record fails
    # closed without it. Evidence is stored encrypted at rest.
    sublet_permission_verified: Mapped[bool] = mapped_column(nullable=False, default=False)
    sublet_permission_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sublet_evidence_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    intro_unlocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    outreach_attempts: Mapped[list["ProviderOutreach"]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="ProviderOutreach.requested_at",
    )
    relay_messages: Mapped[list["RelayMessage"]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="RelayMessage.created_at",
    )
    direct_contact_releases: Mapped[list["DirectContactRelease"]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ProviderOutreach(Base):
    """A single provider-contact attempt (Section 9 of ZR-AI-SEARCH-001).

    Captures the full lifecycle from user request through provider response.
    """

    __tablename__ = "provider_outreach"
    __table_args__ = (
        CheckConstraint(
            "outreach_status IN ('PENDING', 'SENT', 'DELIVERED', 'FAILED', 'SUPPRESSED', 'EXPIRED')",
            name="ck_po_outreach_status",
        ),
        CheckConstraint(
            "channel IN ('EMAIL', 'SMS', 'PLATFORM_MESSAGE', 'TELEPHONE')",
            name="ck_po_channel",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("external_opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=False, index=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    outreach_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    outreach_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_response: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ACCEPTED | DECLINED | NO_RESPONSE
    provider_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_record: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    audit_trail: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Section 9 step 5: what the provider accepted, under which terms version,
    # and that they control the contact the link was sent to.
    acceptance_model: Mapped[str | None] = mapped_column(String(40), nullable=True)
    acceptance_terms_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_contact_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    opportunity: Mapped[ExternalOpportunity] = relationship(back_populates="outreach_attempts")


class RelayMessage(Base):
    """Zoiko-mediated message between a renter and an external provider
    (ZR-AI-SEARCH-001 Phase 2 / Section 11). The relay keeps both parties'
    direct contact details hidden until a bilateral release is granted."""

    __tablename__ = "relay_messages"
    __table_args__ = (
        CheckConstraint(
            "sender_topic IN ('RENTER', 'PROVIDER')",
            name="ck_rm_sender_topic",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("external_opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_topic: Mapped[str] = mapped_column(String(20), nullable=False)
    sender_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sender_handle: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # The renter-provider thread: one per contact request, so a provider's reply
    # reaches the renter who asked (a partner listing can have many requests).
    outreach_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_outreach.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    opportunity: Mapped[ExternalOpportunity] = relationship(back_populates="relay_messages")


class DirectContactRelease(Base):
    """Bilateral consent to release direct contact details between one
    renter and one provider (ZR-AI-SEARCH-001 Section 11). Both parties must
    consent and the market's Legal Pack must allow release. Provider acceptance
    alone is NOT a release, and release is NOT verification."""

    __tablename__ = "direct_contact_release"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("external_opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    outreach_id: Mapped[int | None] = mapped_column(
        ForeignKey("provider_outreach.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    renter_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    both_consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_mask: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    opportunity: Mapped[ExternalOpportunity] = relationship(back_populates="direct_contact_releases")


class ExternalCommercialPolicy(Base):
    """Market-specific commercial policy for external provider conversion (Section 10).

    billing_enabled remains False by default — monetary charging requires both
    a payment-policy amendment and market-specific legal approval.
    """

    __tablename__ = "external_commercial_policy"
    __table_args__ = (
        CheckConstraint(
            "model IN ('CLAIM_AND_LIST', 'FIXED_QUALIFIED_INTRODUCTION_FEE', "
            "'FIXED_SUCCESS_FEE', 'AGENCY_SERVICE_FEE_SHARE', 'PARTNER_REVENUE_SHARE')",
            name="kecp_model",
        ),
        CheckConstraint(
            "provider_type IN ('LANDLORD', 'AGENT', 'MANAGER', 'SOURCE_PARTNER')",
            name="kecp_provider_type",
        ),
        CheckConstraint(
            "trigger_event IN ('PROVIDER_ACCEPTED', 'TENANCY_EXECUTED', 'LISTING_PUBLISHED')",
            name="kecp_trigger_event",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'EXPIRED')",
            name="kecp_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(30), nullable=False)
    fee_amount_minor: Mapped[int | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    fee_share_basis: Mapped[str | None] = mapped_column(String(30), nullable=True)
    trigger_event: Mapped[str] = mapped_column(String(30), nullable=False)
    legal_approval_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_rule_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    billing_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    contract_template_version: Mapped[str] = mapped_column(String(50), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ExternalMarketLegalPack(Base):
    """Per-market activation gate for external discovery and outreach
    (ZR-AI-SEARCH-001 Section 13 "Market Activation Gate").

    No market may enable external search, public web fetch, automated provider
    outreach, direct-contact release, referral fees or sensitive housing
    filters until Legal, Privacy and Commercial have approved its pack. The
    active pack for a market is the latest ACTIVE, fully approved version
    within its effective window; anything else fails closed.
    """

    __tablename__ = "external_market_legal_pack"
    __table_args__ = (
        UniqueConstraint("market_code", "version", name="uq_emlp_market_version"),
        CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'SUSPENDED')", name="ck_emlp_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    market_code: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    legal_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    privacy_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    commercial_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Capabilities (all fail closed).
    external_search_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_visitor_search_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_fetch_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider_outreach_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    permitted_outreach_channels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    direct_contact_release_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    referral_fees_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sensitive_filters_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Market rules.
    prohibited_search_terms: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    lawful_basis_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    privacy_notice_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sender_legal_entity: Mapped[str] = mapped_column(
        String(200), nullable=False, default="Zoiko Realty Group Inc."
    )
    lead_protection_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    terms_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1")

    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ProviderSuppression(Base):
    """Provider opt-out / suppression list (Sections 9.1 and 12 "Direct
    marketing"). A matching entry stops any outreach. Contacts are stored only
    as a SHA-256 of the normalised value, never in plain text."""

    __tablename__ = "provider_suppression"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(40), nullable=False, default="OPT_OUT")
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="provider")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ExternalOpportunityReport(Base):
    """A renter's report that an external lead is stale, inaccurate or
    suspicious (Section 16 external_stale_or_inaccurate_report rate)."""

    __tablename__ = "external_opportunity_report"
    __table_args__ = (
        CheckConstraint("reason IN ('STALE', 'INACCURATE', 'SUSPICIOUS', 'OTHER')", name="ck_eor_reason"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("external_opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reporter_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
