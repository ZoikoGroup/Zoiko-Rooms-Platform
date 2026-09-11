from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ZR-ENG-CLR-002 Section 13.2 / ZR-ENG-CLR-003 Section 13.2's "confidence" field --
# this is the honest signal that VERIFIED means "checked against real regulator
# guidance", not just "someone typed a number in". Every row this platform ships
# with today is REVIEW_REQUIRED: reasonable placeholder values, not a substitute
# for actual legal review before any of this governs real money or real tenancies.
MARKET_POLICY_CONFIDENCE_LEVELS = ("VERIFIED", "REVIEW_REQUIRED", "DEPRECATED", "EMERGENCY_BLOCK")

DEPOSIT_CUSTODY_MODELS = ("STATUTORY_SCHEME", "GOVERNMENT_BOND", "REGULATED_ESCROW", "TRUST_ACCOUNT", "HOST_OR_AGENT", "OTHER_APPROVED")
CONSENT_STANDARDS = ("HOST_ABSOLUTE_DISCRETION", "REASONABLE_REFUSAL_ONLY", "NOTICE_ONLY", "STATUTORY_RESPONSE_DEADLINE")
PAYEE_MODELS = ("ORIGINAL_RENTER_PAYEE", "HOST_OR_LANDLORD_PAYEE", "AUTHORIZED_AGENT_PAYEE", "SPLIT_PAYEE", "EXTERNAL_PAYEE_RECORDED")

# ZR-ENG-CLR-005 Section 9.1/AC-20/AC-35: the named funds-flow profiles the
# spec defines. Only DIRECT_SETTLEMENT is actually implementable end-to-end
# in this build -- PSP_DEFERRED_PAYOUT/TRUST_ESCROW_CUSTODY need a real PSP
# or trust partner this codebase doesn't have, and ZOIKO_REGULATED_CUSTODY is
# "OFF by default; requires explicit licensing/perimeter approval" per spec.
# A market pack resolving to any profile outside SUPPORTED_FUNDS_FLOW_PROFILES
# fails closed at payout time (crud/finance.py:run_payout) rather than
# silently defaulting to direct settlement or Zoiko custody.
FUNDS_FLOW_PROFILES = (
    "DIRECT_SETTLEMENT", "PSP_DEFERRED_PAYOUT", "TRUST_ESCROW_CUSTODY", "ZOIKO_REGULATED_CUSTODY", "EXTERNAL_OFF_PLATFORM",
)
SUPPORTED_FUNDS_FLOW_PROFILES = ("DIRECT_SETTLEMENT",)


class MarketPolicyPack(Base):
    """One jurisdiction's resolved rule set for deposits and subletting, versioned
    and effective-dated (ZR-ENG-CLR-002 Section 3.2 / ZR-ENG-CLR-003 Section 13.2).
    Deposit/sublet code must resolve values from here -- never branch on a
    jurisdiction_code string directly (both docs' explicit "no hard-coded country
    branches" rule)."""

    __tablename__ = "market_policy_packs"

    id: Mapped[int] = mapped_column(primary_key=True)
    jurisdiction_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    confidence: Mapped[str] = mapped_column(String(20), default="REVIEW_REQUIRED")
    legal_source_note: Mapped[str] = mapped_column(String(2000), default="")

    # -- Payment policy (ZR-ENG-CLR-005 Section 8, AC-09: platform fees are
    # resolved from effective-dated policy, not a hard-coded rate). Fraction,
    # not a percentage -- 0.10 means 10%. Host-paid percentage-of-rent fee is
    # the only fee basis this MVP implements (Section 8.1's payer/basis/
    # tiered/hybrid dimensions are deferred until a market pack actually
    # needs them; renter fees stay OFF by default with no toggle here yet).
    platform_fee_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0.10)
    funds_flow_profile: Mapped[str] = mapped_column(String(30), default="DIRECT_SETTLEMENT")
    # ZR-ENG-CLR-005 Section 13.1/AC-26: "Service-fee invoice issuer is the
    # correct Zoiko legal entity and tax configuration." Resolved per
    # jurisdiction/effective-date like every other field here, never
    # hard-coded in the invoice-generation code itself -- see
    # crud/finance.py:_generate_service_fee_invoice_pdf. tax_rate defaults to
    # 0.0 (no tax registration/authority integration exists in this build);
    # showing 0% honestly is the correct "tax configuration" for a market
    # pack that has none, not an invented placeholder rate.
    zoiko_legal_entity_name: Mapped[str] = mapped_column(String(200), default="Zoiko Realty Group")
    zoiko_tax_registration_number: Mapped[str] = mapped_column(String(50), default="")
    service_fee_tax_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0.0)

    # -- Deposit policy (Section 2) --
    deposit_instrument_allowed: Mapped[str] = mapped_column(String(20), default="OPTIONAL")
    deposit_max_rent_multiple: Mapped[float] = mapped_column(Numeric(6, 2), default=3.0)
    deposit_custody_model: Mapped[str] = mapped_column(String(30), default="HOST_OR_AGENT")
    deposit_protection_deadline_days: Mapped[int | None] = mapped_column(nullable=True)
    deposit_release_deadline_days: Mapped[int] = mapped_column(default=30)

    # -- Sublet policy (Section 3) --
    sublet_consent_standard: Mapped[str] = mapped_column(String(30), default="STATUTORY_RESPONSE_DEADLINE")
    sublet_consent_response_days: Mapped[int] = mapped_column(default=14)
    sublet_max_rent_multiple_of_original: Mapped[float] = mapped_column(Numeric(6, 2), default=1.0)
    sublet_assignment_payee_model: Mapped[str] = mapped_column(String(30), default="HOST_OR_LANDLORD_PAYEE")
    sublet_sublease_payee_model: Mapped[str] = mapped_column(String(30), default="ORIGINAL_RENTER_PAYEE")

    # -- Booking-change / rent-change policy (Section 8, ZR-ENG-CLR-008 §10/AC-24) --
    # Both the doc's own validation examples cite a minimum interval, not a
    # blanket ban: NSW "rent increases are generally limited to once in 12
    # months", Ontario "rent increases are subject to timing[...] rules". 365
    # is a reasonable default, not a verified legal figure for any specific
    # jurisdiction -- same REVIEW_REQUIRED honesty as every other field here.
    rent_change_min_interval_days: Mapped[int] = mapped_column(default=365)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
