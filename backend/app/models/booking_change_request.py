from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.services.booking_change_state_machine import BOOKING_CHANGE_STATUSES  # noqa: F401 -- re-exported for callers

# ZR-ENG-CLR-008 Section 4: DATE_SHIFT (move-in date, before the renter has
# actually moved in), EXTENSION (move-out date pushed later, after move-in),
# SHORTENING (move-out date pulled earlier -- but only before move-in;
# Section 7.4/AC-10 route a post-commencement early end to Section 6
# termination instead of letting it through as a disguised "amendment", and
# this codebase's Section 6 termination flow already exists via
# crud/occupancy.py:end_occupancy, so SHORTENING here deliberately refuses
# once an Occupancy exists rather than reimplementing that routing), and
# PREMISES_CHANGE (different room/property -- Section 14: "usually requires
# a new booking/version family... do not simply overwrite property
# identifier". Unlike the other three, approval here does NOT drive the
# amendment engine -- it opens a fresh Application/Offer/Agreement on the
# target listing via the ordinary leasing pipeline, so pricing/compliance
# get genuinely re-evaluated rather than copied. See
# resulting_application_id/target_listing_id below and crud/leasing.py's
# _complete_premises_change_migration_if_applicable hook, which ends/voids
# the original booking only once that *new* agreement reaches SIGNED --
# "preserve old booking until replacement commit policy permits release").
# Occupant/party change is still out of scope -- it already has its own
# dedicated flow (crud/sublet.py's SubletRequest, AC-13), so a Section 8
# request for it is refused and pointed there rather than duplicated.
#
# FINANCIAL_CHANGE (Section 10/AC-24 "rent_change_rules"): a renter requesting
# a new monthly rent on their signed agreement. Reuses the amendment engine
# exactly like EXTENSION/SHORTENING (monthlyRent is already one of its
# _PROPOSABLE_TERM_KEYS), but is gated by a real jurisdiction policy field
# (MarketPolicyPack.rent_change_min_interval_days) -- the doc's own NSW/
# Ontario examples both cite a minimum interval between rent changes, so this
# is the one concrete "rent_change_rules" enforcement point this MVP adds.
# Available before or after move-in, like PREMISES_CHANGE.
#
# TERM_SHIFT (Section 4: 'Both start and end move'): before move-in only,
# like DATE_SHIFT -- moving the *start* date after the renter has already
# moved in has no meaning. Reuses the amendment engine exactly like the
# others, but proposes startDate AND termMonths together in one call (the
# engine already merges independent snapshot fields from one proposed_terms
# dict -- see agreement_amendments.py:_merged_snapshot), which is what
# distinguishes it from DATE_SHIFT (start only, term preserved) and
# EXTENSION/SHORTENING (term only, start preserved).
#
# LEGAL_ORDER_CHANGE (Section 4: 'Court, regulator or statutory change
# requiring amendment | System/Admin initiated; authority evidence
# mandatory.'). The one change type this MVP lets an admin create directly
# rather than a renter -- Section 6 Authority/Consent Matrix's own row for
# "System": 'Expiry, policy-required transformations... No human consent
# substitution... only through deterministic policy and recorded gates.' The
# admin's authority_evidence_ref *is* that recorded gate, so this skips
# AWAITING_HOST/AWAITING_RENTER entirely and starts life already at
# AWAITING_AGREEMENT_ACTION -- there is no host decision to wait for when a
# court/regulator has already decided.
#
# DEPOSIT_CHANGE (Section 4/11: 'Deposit amount/instrument/custody terms --
# Section 2 authoritative; never auto-mutated.'). Deliberately request/decide
# ONLY -- approving this BCR never touches DepositRecord.held_amount itself.
# Section 2 (crud/finance.py's release_deposit/submit_deposit_claim) stays
# the one and only way deposit money actually moves; this just gives the
# ask a tracked, audited paper trail and jumps straight from AWAITING_HOST
# to EFFECTIVE on approval (see state machine) since there is no agreement
# action of this codebase's own to wait through.
BOOKING_CHANGE_TYPES = (
    "DATE_SHIFT", "EXTENSION", "SHORTENING", "PREMISES_CHANGE", "FINANCIAL_CHANGE", "TERM_SHIFT",
    "LEGAL_ORDER_CHANGE", "DEPOSIT_CHANGE",
)

# See app/services/booking_change_state_machine.py for the full state set,
# the transition graph, and which of Section 16's states this MVP does not
# yet implement (and why).


class BookingChangeRequest(Base):
    """A renter's request to shift their agreed move-in date (before moving
    in) or extend their stay (after moving in). Anchored to the Agreement,
    not an Occupancy -- DATE_SHIFT has no Occupancy row yet at request time,
    and EXTENSION's Occupancy is only reached and updated once the resulting
    amendment goes EFFECTIVE (see crud/leasing.py's post-signature amendment
    hook). Approval drives the existing AgreementAmendment engine
    (crud/agreement_amendments.py) for the actual contract re-papering +
    re-signature, rather than duplicating that machinery -- this model exists
    to carry the renter-initiated request/decision trail the (admin-only,
    already-classified-on-entry) amendment engine has no room for on its own.

    original_start_date/proposed_start_date always describe the agreement's
    operative start date -- for EXTENSION, where the start date does not
    change, both are simply set to the current start date; the actual
    proposed change lives in original_end_date/proposed_end_date instead."""

    __tablename__ = "booking_change_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by_guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    change_type: Mapped[str] = mapped_column(String(20), default="DATE_SHIFT")
    status: Mapped[str] = mapped_column(String(25), default="AWAITING_HOST")
    original_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    proposed_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    # EXTENSION/SHORTENING/TERM_SHIFT only -- see class docstring.
    original_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    proposed_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # EXTENSION/SHORTENING: a signed DELTA in months (positive/negative).
    # TERM_SHIFT: the new term's ABSOLUTE month count -- a delta relative to
    # the old term is meaningless once the start date is also moving. Which
    # interpretation applies is entirely determined by change_type; see
    # crud/booking_change_requests.py:_proposed_terms_and_default_reason.
    additional_term_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PREMISES_CHANGE only.
    target_listing_id: Mapped[str | None] = mapped_column(ForeignKey("listings.id"), nullable=True)
    # Set once approval opens the replacement Application -- distinct from
    # resulting_amendment_id, since this change type never touches the
    # amendment engine at all.
    resulting_application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    # FINANCIAL_CHANGE only -- the real financial-delta figures a UI can show
    # old vs new vs difference from, before consent, per Section 1's "No party
    # may be financially worse off by a hidden recalculation" doctrine.
    original_monthly_rent: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    proposed_monthly_rent: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    # DEPOSIT_CHANGE only -- the currently-held amount and what's being
    # asked for, purely for display/audit; approving never writes to
    # DepositRecord.held_amount from these values (see class comment above).
    original_deposit_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    proposed_deposit_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    decision_note: Mapped[str] = mapped_column(String(500), default="")
    decided_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once approval drives the amendment engine -- lets a caller follow
    # this request through to the resulting AgreementAmendment/re-signature.
    resulting_amendment_id: Mapped[int | None] = mapped_column(ForeignKey("agreement_amendments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Doc Section 16 'State rule: Expiry -- provisional inventory and
    # unsigned proposals have explicit expiry times.' Fixed 7-day window for
    # this MVP; no jurisdiction/market-policy field for it yet.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # AC-16/AC-17: "Consent records bind to exact proposal version/hash" and
    # "material proposal change invalidates prior consent". Computed once at
    # creation from this row's own proposed_* fields (see
    # app/services/booking_change_consent.py) and re-verified immediately
    # before approval acts on them -- catches the row having been mutated
    # in between (directly in the DB, or by a future code path) rather than
    # silently approving different terms than the renter actually proposed.
    proposal_hash: Mapped[str] = mapped_column(String(64), default="")
    # LEGAL_ORDER_CHANGE only -- mandatory per Section 4/6; see class-level comment.
    authority_evidence_ref: Mapped[str] = mapped_column(String(1024), default="")

    agreement: Mapped["Agreement"] = relationship()
    requested_by: Mapped["Guest"] = relationship()
    decided_by: Mapped["AdminUser"] = relationship()
    resulting_amendment: Mapped["AgreementAmendment"] = relationship()
    target_listing: Mapped["Listing"] = relationship()
    resulting_application: Mapped["Application"] = relationship()
