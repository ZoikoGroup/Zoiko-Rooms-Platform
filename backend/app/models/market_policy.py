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

# ZR-ENG-CLR-002 Section 2/13.2: whether a jurisdiction permits a deposit
# instrument at all for this market. REQUIRED/PROHIBITED were previously
# stored but never read by any business logic -- every pack this platform has
# ever created uses OPTIONAL (the model default), and crud/leasing.py:
# add_offer_terms validated only the deposit *amount* against the cap, never
# whether a deposit was permitted in the first place.
DEPOSIT_INSTRUMENT_ALLOWED_VALUES = ("REQUIRED", "OPTIONAL", "PROHIBITED", "NOT_APPLICABLE")

DEPOSIT_CUSTODY_MODELS = ("STATUTORY_SCHEME", "GOVERNMENT_BOND", "REGULATED_ESCROW", "TRUST_ACCOUNT", "HOST_OR_AGENT", "OTHER_APPROVED")
CONSENT_STANDARDS = ("HOST_ABSOLUTE_DISCRETION", "REASONABLE_REFUSAL_ONLY", "NOTICE_ONLY", "STATUTORY_RESPONSE_DEADLINE")
PAYEE_MODELS = ("ORIGINAL_RENTER_PAYEE", "HOST_OR_LANDLORD_PAYEE", "AUTHORIZED_AGENT_PAYEE", "SPLIT_PAYEE", "EXTERNAL_PAYEE_RECORDED")

# ZR-ENG-CLR-005 Section 9.1/AC-20/AC-35: the named funds-flow profiles the
# spec defines. DIRECT_SETTLEMENT and PSP_DEFERRED_PAYOUT are the two this
# build can actually execute end-to-end -- the former via the legacy
# PayoutBeneficiary/bank-details path, the latter via real Stripe Connect
# (crud/host_stripe_account.py + crud/finance.py:run_payout's deferred-until-
# move-in-confirmed gate). TRUST_ESCROW_CUSTODY needs a real trust partner
# this codebase doesn't have, and ZOIKO_REGULATED_CUSTODY is "OFF by default;
# requires explicit licensing/perimeter approval" per spec -- neither is
# something app code may fake into existence. A market pack resolving to any
# profile outside SUPPORTED_FUNDS_FLOW_PROFILES fails closed at payout time
# (crud/finance.py:run_payout) rather than silently defaulting to direct
# settlement or Zoiko custody.
FUNDS_FLOW_PROFILES = (
    "DIRECT_SETTLEMENT", "PSP_DEFERRED_PAYOUT", "TRUST_ESCROW_CUSTODY", "ZOIKO_REGULATED_CUSTODY", "EXTERNAL_OFF_PLATFORM",
)
SUPPORTED_FUNDS_FLOW_PROFILES = ("DIRECT_SETTLEMENT", "PSP_DEFERRED_PAYOUT")

# ZR-ENG-CLR-006 Section 11.1's full liability-model taxonomy, modeled
# completely (same "never trim the taxonomy at the data layer" discipline as
# TERMINATION_CAUSE_CODES) even though crud/refund_entitlement.py's
# _compute_policy_liability only has a real formula for a subset --
# TRIBUNAL_OR_COURT_DETERMINED is never selected here (a tribunal amount is
# always a Super Admin's own entry, crud/termination.py:set_tribunal_liability,
# and always takes precedence over whatever this field says once entered --
# see _compute_policy_liability's own docstring for why that isn't additive
# double-counting).
TERMINATION_LIABILITY_MODELS = (
    "NOTICE_RENT", "STATUTORY_BREAK_FEE", "CONTRACT_BREAK_AMOUNT", "ACTUAL_REASONABLE_LOSS",
    "CAPPED_COMPENSATION", "ZERO_LIABILITY", "TRIBUNAL_OR_COURT_DETERMINED", "MIXED",
)


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
    # ZR-PAY-CFG-001 Decision 3: removed from Zoiko Rooms -- there is no
    # percentage of rent. Kept only as an always-NULL column (migration
    # b7d2e4f1a9c3 cleared existing values); the API rejects any value and
    # crud/finance.py never reads it.
    platform_fee_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True, default=None)
    funds_flow_profile: Mapped[str] = mapped_column(String(30), default="DIRECT_SETTLEMENT")
    # ZR-ENG-CLR-005 Section 12.1/12.2/AC-12: "Payment method availability is
    # the intersection of... market pack... computed server-side... the UI
    # must never show a method the backend cannot lawfully or operationally
    # execute." Empty list (the honest default for an unconfigured market)
    # falls back to CARD-only in the resolver, not to every method class --
    # a market pack must explicitly opt into the wider set (e.g. India's own
    # UPI-class LOCAL_REAL_TIME rail), same fail-closed-to-the-safe-minimum
    # posture as every other list field on this pack.
    permitted_payment_method_classes: Mapped[list[str]] = mapped_column(JSON, default=list)
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
    # Section 5 gap: an UPFRONT-cadence offer previously billed the entire
    # lease term as advance rent with no jurisdiction ceiling at all -- a
    # multi-year lease could collect that many years' rent in one lump-sum
    # obligation. Default (12) is deliberately generous, same as
    # deposit_max_rent_multiple's own non-zero default above -- it's a real
    # ceiling against a genuinely runaway term, not a retroactive block on
    # this platform's existing, intentional "bill the whole term upfront"
    # UPFRONT cadence for an ordinary lease length. Enforced in
    # crud/leasing.py:add_offer_terms against
    # OfferTermsCreate.term_months whenever cadence == "UPFRONT".
    advance_rent_max_months: Mapped[int] = mapped_column(default=12)

    # -- Sublet policy (Section 3) --
    sublet_consent_standard: Mapped[str] = mapped_column(String(30), default="STATUTORY_RESPONSE_DEADLINE")
    sublet_consent_response_days: Mapped[int] = mapped_column(default=14)
    sublet_max_rent_multiple_of_original: Mapped[float] = mapped_column(Numeric(6, 2), default=1.0)
    sublet_assignment_payee_model: Mapped[str] = mapped_column(String(30), default="HOST_OR_LANDLORD_PAYEE")
    sublet_sublease_payee_model: Mapped[str] = mapped_column(String(30), default="ORIGINAL_RENTER_PAYEE")

    # ZR-SUB-003 Section 8's own jurisdiction-config-key table -- the UI/
    # terminology/duration/document/signature/retention keys that table's own
    # sublet_consent_*/sublet_*_payee_model fields above don't cover.
    # crud/market_policy.py:to_policy_snapshot captures these at submission
    # time the same way as every other jurisdiction-resolved field.
    sublet_ui_term: Mapped[str] = mapped_column(String(30), default="sublet")
    # Null = no jurisdiction-configured cap (this MVP's original, unbounded
    # behavior) -- never silently invented as a number no jurisdiction
    # actually specified.
    sublet_max_duration_months: Mapped[int | None] = mapped_column(nullable=True)
    sublet_required_fields: Mapped[list] = mapped_column(JSON, default=list)
    sublet_required_documents: Mapped[list] = mapped_column(JSON, default=list)
    # "None, acknowledgment, e-signature or external legal workflow" --
    # crud/sublet.py:approve_sublet_request only actually enforces
    # E_SIGNATURE today (for the co-tenancy path's new agreement); NONE/
    # ACKNOWLEDGMENT/EXTERNAL_LEGAL_WORKFLOW are modeled but not yet gated
    # on (same never-trim-the-taxonomy discipline as NOTICE_METHODS).
    sublet_signature_mode: Mapped[str] = mapped_column(String(30), default="NONE")
    sublet_notice_requirements: Mapped[str] = mapped_column(String(500), default="")
    sublet_retention_class: Mapped[str] = mapped_column(String(30), default="STANDARD")
    sublet_additional_gates: Mapped[list] = mapped_column(JSON, default=list)

    # -- Termination policy (ZR-ENG-CLR-006 Section 6/AC-03: "No global fixed
    # notice period is hard-coded" -- this is the configurable value the
    # Termination Policy Resolver reads instead. Reasonable placeholder for
    # ordinary renter-initiated early exit under common Indian rental
    # practice -- REVIEW_REQUIRED confidence like every other field here, not
    # counsel-validated; the only cause this MVP actually resolves a notice
    # period for -- see models/termination_case.py:UNILATERAL_CAUSE_CODES.
    termination_notice_days: Mapped[int] = mapped_column(default=30)
    # ZR-ENG-CLR-006 Section 10: "rent-cycle alignment -- some markets align
    # termination to rent cycle; others do not. Configurable." Off by
    # default (raw notice-days date, unchanged behavior) -- when a market
    # pack turns this on, crud/termination.py:_align_to_rent_cycle rounds
    # the computed date up to the end of its calendar month, a reasonable
    # placeholder cycle boundary for this build's only real cadence
    # (MONTHLY), not a verified market rule.
    align_termination_to_rent_cycle: Mapped[bool] = mapped_column(default=False)
    # ZR-ENG-CLR-006 Section 11.1/AC-12: which liability model applies to an
    # ordinary renter-initiated early exit or contract break beyond the
    # earned/unearned rent split NOTICE_CAUSE_CODES already produces for free
    # (see models/termination_case.py's own NOTICE_CAUSE_CODES docstring --
    # that mechanism IS this field's NOTICE_RENT value, its default: no extra
    # charge on top of rent through the notice period). REVIEW_REQUIRED
    # placeholder like every field on this row, not counsel-validated.
    termination_liability_model: Mapped[str] = mapped_column(String(30), default="NOTICE_RENT")
    # Break-fee/contract-break/capped-compensation formula input: a multiple
    # of one month's rent (crud/refund_entitlement.py:_monthly_rent_amount),
    # the same "multiple of rent" idiom deposit_max_rent_multiple already
    # uses above. 0 (default) means no extra fee even if a model other than
    # NOTICE_RENT/ZERO_LIABILITY is selected -- a market pack must set this
    # to actually charge one.
    termination_break_fee_rent_multiple: Mapped[float] = mapped_column(Numeric(6, 2), default=0.0)
    # CAPPED_COMPENSATION's own ceiling, expressed the same way. Null means
    # uncapped (the break-fee multiple above stands unmodified).
    termination_liability_cap_rent_multiple: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    # Section 6 gap: some markets don't charge a single flat break-fee
    # multiple -- the fee tapers down the further into the lease the renter
    # got before leaving (e.g. "1.0x rent if <3 months in, 0.5x if 3-6
    # months, 0x after 6 months"). Empty list (default) means "no bands
    # configured" -- crud/refund_entitlement.py:_compute_policy_liability
    # falls back to the single flat termination_break_fee_rent_multiple
    # above unchanged, so this is purely additive, never a behavior change
    # for a market pack that hasn't opted in. Each entry is
    # {"maxElapsedMonths": int, "multiple": float}; entries are matched in
    # ascending maxElapsedMonths order against whole months elapsed since
    # move-in, first match wins -- elapsed time beyond every band's own
    # ceiling means no fee (the tenancy has run long enough that the
    # tapering schedule has run out).
    termination_break_fee_bands: Mapped[list] = mapped_column(JSON, default=list)

    # -- Pre-move-in cancellation policy (Section 7 gap) --
    # A signed-but-not-moved-in booking is a genuinely different situation
    # from an active tenancy (Occupancy.CANCELLED, not ENDED) and has its
    # own, separate policy dimension -- see crud/occupancy.py:
    # cancel_before_move_in. A free-cancellation window measured from
    # booking creation (Occupancy.created_at), inside which a cancellation
    # is always a full refund regardless of the fee multiple below.
    pre_move_in_free_cancellation_hours: Mapped[int] = mapped_column(default=24)
    # Outside the free window: a flat fee (same "multiple of one month's
    # rent" idiom as termination_break_fee_rent_multiple), deducted from
    # whatever was already paid before the remainder is refunded. 0
    # (default) means full refund even outside the free window -- a market
    # pack must set this to actually charge a fee.
    pre_move_in_cancellation_fee_rent_multiple: Mapped[float] = mapped_column(Numeric(6, 2), default=0.0)

    # -- Host entry-notice policy (Section 9 gap) --
    # Previously nothing anywhere in this codebase modeled a Host's right to
    # enter an occupied unit for inspection/repair, or how much advance
    # notice they owe the tenant first -- see models/host_entry_visit.py.
    # 24 hours is a common real-world minimum (not a verified legal figure
    # for any specific jurisdiction -- same REVIEW_REQUIRED honesty as every
    # other number on this pack); crud/host_entry_visit.py:schedule_entry_
    # visit enforces it, with a real, auditable emergency-entry exception
    # (fire, flood, gas leak, etc.) that bypasses it.
    entry_notice_hours: Mapped[int] = mapped_column(default=24)

    # -- Holdover policy (Section 9 gap) --
    # An ACTIVE occupancy reaching its own expected_end_date with no
    # renewal/termination case was (and by default still is) a silent
    # no-op: rent generation (crud/occupancy.py:generate_next_rent_obligation)
    # simply stops billing. False by default to exactly preserve that
    # existing, deliberately-tested behavior
    # (tests/test_occupancy_crud.py::test_refuses_to_run_past_the_leases_
    # expected_end_date) -- this is opt-in per jurisdiction, not a default
    # behavior change. What's newly built regardless of this flag is
    # visibility: crud/occupancy.py:list_occupancies_in_holdover surfaces
    # any ACTIVE occupancy past its own expected_end_date to staff, whether
    # or not billing continues for it.
    holdover_allowed: Mapped[bool] = mapped_column(default=False)
    # A holdover premium, expressed as a multiple applied to the normal rent
    # amount (1.0 = no premium, the default). Only read when holdover_allowed
    # is true. Common real-world practice charges more for holding over past
    # lease end.
    holdover_rent_multiple: Mapped[float] = mapped_column(Numeric(6, 2), default=1.0)

    # -- Booking-change / rent-change policy (Section 8, ZR-ENG-CLR-008 §10/AC-24) --
    # Both the doc's own validation examples cite a minimum interval, not a
    # blanket ban: NSW "rent increases are generally limited to once in 12
    # months", Ontario "rent increases are subject to timing[...] rules". 365
    # is a reasonable default, not a verified legal figure for any specific
    # jurisdiction -- same REVIEW_REQUIRED honesty as every other field here.
    rent_change_min_interval_days: Mapped[int] = mapped_column(default=365)

    # -- Dispute forum policy (ZR-ENG-CLR-010 Section 6/7) -- per-jurisdiction
    # override of app/services/dispute_forum_resolver.py's static claim-family
    # -> authority-class defaults. Defaults here match that resolver's own
    # hardcoded values exactly, so a market pack that never sets these changes
    # nothing -- same REVIEW_REQUIRED honesty as every other field on this
    # model: reasonable MVP defaults, not counsel-verified per-market
    # determinations. ZOIKO_SERVICE (always A0) and every claim family this
    # MVP has no forum mapping for at all (PROTECTED_SAFETY, VERIFICATION_FRAUD,
    # MARKETPLACE_CONDUCT, PAYMENT, REFUND_PAYOUT) are deliberately not
    # configurable here -- jurisdiction can't manufacture a mapping that
    # doesn't exist, and Zoiko's own service-fee authority isn't a
    # jurisdiction question.
    dispute_deposit_authority_class: Mapped[str] = mapped_column(String(2), default="A2")
    dispute_booking_agreement_authority_class: Mapped[str] = mapped_column(String(2), default="A1")
    dispute_property_condition_authority_class: Mapped[str] = mapped_column(String(2), default="A1")
    dispute_sublet_occupancy_authority_class: Mapped[str] = mapped_column(String(2), default="A1")

    # -- Dispute deadlines/conciliation/waiver policy (Section 7/20/26/29 --
    # AC-28/AC-29, QA-Q21/Q22/Q23). Same REVIEW_REQUIRED honesty as every
    # other field on this model: reasonable MVP defaults, not
    # counsel-verified per-market determinations. Nothing here invents a
    # requirement a market hasn't actually configured -- see each field's
    # own comment for its "nothing configured" behavior.
    #
    # Section 26: "recommended commercial default 5 business days only
    # where no statutory/forum rule supersedes it" -- this field IS that
    # per-market override point; the hardcoded 5 stays the fallback for an
    # occupancy with no resolvable market pack at all.
    dispute_response_window_days: Mapped[int] = mapped_column(default=5)
    dispute_evidence_window_days: Mapped[int] = mapped_column(default=14)
    # Null means "no statutory filing deadline configured for this
    # market" -- crud/dispute_external_proceeding.py never invents one; a
    # configured value only ever gets used to honestly RECORD whether a
    # filing landed after it (QA-Q21: "does not invent an extension;
    # routes according to forum rules"), never to block or reject a filing
    # outright (Zoiko has no authority to decide that -- the external
    # forum does).
    dispute_external_filing_deadline_days: Mapped[int | None] = mapped_column(nullable=True)
    # Section 7 "Forum route: required pre-action notice; mandatory/
    # optional conciliation" / QA-Q22/Q23. NOT_REQUIRED is the default and
    # matches the doc's own "NO UNIVERSAL ARBITRATION... do not implement
    # a global mandatory-arbitration fallback" rule directly -- a market
    # pack must opt IN to a conciliation requirement, never the reverse.
    dispute_conciliation_requirement: Mapped[str] = mapped_column(String(20), default="NOT_REQUIRED")
    # AC-28: "cannot silently waive non-waivable rights where the market
    # pack prohibits that result." Empty list (the default) means no
    # claim family is known to be non-waivable in this market -- honest
    # absence of configuration, not a claim that nothing is ever
    # non-waivable anywhere. A market pack that lists a claim family here
    # makes crud/dispute_settlement.py's own waiver-acknowledgment
    # checkbox (acknowledges_no_nonwaivable_waiver) a real, enforced block
    # instead of only a self-certified attestation for that family.
    dispute_non_waivable_claim_families: Mapped[list] = mapped_column(JSON, default=list)
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
    # Section 12 gap: identity verification was the only evidence type with
    # any retention_days concept at all -- dispute evidence (photos,
    # documents backing a claim) had hold/redact/manual-delete but no
    # time-bound expiry. Nullable, default None -- "not configured, no
    # automatic sweep for this jurisdiction" (see
    # services/evidence_retention.py:sweep_expired_dispute_evidence), never
    # a behavior change for a market pack that hasn't opted in. An ACTIVE
    # legal hold always overrides this and blocks deletion regardless.
    dispute_evidence_retention_days: Mapped[int | None] = mapped_column(nullable=True)
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
