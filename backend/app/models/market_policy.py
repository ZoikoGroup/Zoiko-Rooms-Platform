from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Numeric, String
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

    # -- Verification policy (Section 12, ZR-ENG-CLR-012) --
    # Doc's own AC-16/Section 9: "There is no global 'right to rent' check.
    # Occupancy eligibility exists only where a jurisdiction imposes it...
    # must not export England-specific immigration checking to other
    # markets." False by default -- only England's row (seeded separately)
    # sets this True. This is the Requirement Resolver's one live input for
    # OCCUPANCY_ELIGIBILITY; app code must never branch on jurisdiction_code
    # directly to decide this (same "no hard-coded country branches" rule
    # as deposit/sublet policy above).
    occupancy_eligibility_required: Mapped[bool] = mapped_column(default=False)
    # Doc Section 34 (GOV.UK validation example): England's real mechanism is
    # either an online "share code" lookup or an accepted manual document
    # check -- both are real, publicly documented UK government routes, not
    # invented ones. Stored as free text here since this MVP has no live
    # Home Office API integration; every check is manual (verifier_admin_id
    # on OccupancyEligibilityCheck), same honesty as the rest of this pack.
    occupancy_eligibility_method_note: Mapped[str] = mapped_column(String(200), default="")
    # Doc Section 9: "Time-limited eligibility creates a FOLLOW_UP_REQUIRED
    # date." Null means the check (if required) does not expire on its own.
    occupancy_eligibility_follow_up_days: Mapped[int | None] = mapped_column(nullable=True)
    # Doc Section 28: "Documents are never stored permanently by default."
    # Applies to the raw IdentityVerification evidence file, not the
    # credential/decision record (retained separately, indefinitely, for
    # audit -- see VerificationCredential). REVIEW_REQUIRED-level default,
    # like every other number in this pack.
    identity_evidence_retention_days: Mapped[int] = mapped_column(default=90)
    # Doc Section 14: "Property compliance should be modeled as structured
    # credentials, not attachment folders." Which certificate/registration
    # classes are actually mandatory (gas safety, EPC, HMO license, etc.) is
    # real per-jurisdiction law this MVP has no legal sign-off on -- so this
    # resolves to an empty list by default (no gate at all, same fail-open
    # posture as occupancy_eligibility_required) until a market pack is
    # explicitly configured with the codes its own legal review approved.
    # Never hard-code a specific code's meaning in app code; the resolver
    # only knows "this jurisdiction requires credential code X to publish."
    required_property_compliance_codes: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Doc Sections 5/AC-03: "Application can proceed before full ID
    # completion unless the active market pack explicitly requires an
    # earlier gate." False by default -- same fail-open-to-"nothing extra
    # required" posture as occupancy_eligibility_required above; a
    # jurisdiction whose law genuinely requires identity before an
    # application can be submitted (rather than only before confirmation)
    # opts in here explicitly.
    identity_required_at_application: Mapped[bool] = mapped_column(default=False)

    # Doc Sections 10/11: unlike occupancy eligibility (mandatory where
    # imposed), screening/affordability checks are Host-optional but
    # jurisdiction-CONSTRAINED -- "No global criminal-record, eviction-
    # history or credit check." This is a permission list, not a
    # requirement list: a Host may request any check_type NOT named here.
    # Empty by default (no prohibition configured yet) rather than
    # permitting or banning specific categories platform-wide -- the
    # doc's own AC-17 "no hard-coded country branches", applied here.
    screening_prohibited_check_types: Mapped[list[str]] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
