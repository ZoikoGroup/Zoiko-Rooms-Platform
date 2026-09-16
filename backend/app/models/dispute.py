from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-010 Section 5: the claim-family taxonomy, modeled in full even
# though app/services/dispute_forum_resolver.py only has a real authority
# mapping for a subset -- same "never trim the taxonomy at the data layer"
# discipline as TERMINATION_LIABILITY_MODELS (models/market_policy.py).
# PAYMENT/REFUND_PAYOUT claims are recorded here for case-level visibility,
# but a chargeback specifically is still adjudicated through the existing
# finance.DisputeCase/PSP-reversal mechanism -- see DisputeResolutionCase's
# own docstring below for why these two objects stay separate.
DISPUTE_CLAIM_FAMILIES = (
    "DEPOSIT", "PAYMENT", "REFUND_PAYOUT", "PROPERTY_CONDITION", "BOOKING_AGREEMENT",
    "SUBLET_OCCUPANCY", "MARKETPLACE_CONDUCT", "PROTECTED_SAFETY", "VERIFICATION_FRAUD", "ZOIKO_SERVICE",
)

# Section 6: the full authority-class ladder. Only A0/A1/A2/A6 are ever
# actually produced by the MVP resolver today (see dispute_forum_resolver.py)
# -- A3 (payment rail/PSP) stays the existing finance chargeback path, A4/A5
# have no adapter in this build yet and resolve to LEGAL_REVIEW_REQUIRED
# instead of being guessed at.
AUTHORITY_CLASSES = ("A0", "A1", "A2", "A3", "A4", "A5", "A6")

DISPUTE_CLAIMANT_ROLES = ("RENTER", "HOST")

# ZR-ENG-CLR-010 AC-35/36/37/38: a claim in one of these families
# conceptually arises from a real, already-existing record elsewhere in
# this codebase -- crud/disputes.py's own _resolve_source_record
# auto-links to it (the most relevant existing row for the claim's
# occupancy) rather than leaving the claim floating with no connection to
# the underlying incident/request/handover it's actually about. Same
# source_type/source_id labeled-polymorphic-reference shape as
# finance.py's FinancialHold/LedgerEntry (no real FK constraint, since it
# can point at one of several different tables) -- not the "exactly one of
# N FK columns" shape DisputeResolutionCase uses for opened_by_guest_id/
# opened_by_party_id, which only ever has a fixed two-way choice.
# OCCUPANCY_HANDOVER_EVENT is AC-38's own "preserve Section 9 possession
# and access evidence" for a LOCKOUT/ACCESS/HOLDOVER claim code within the
# SUBLET_OCCUPANCY family -- an honest subset (move-in/move-out handover
# evidence only, this codebase's only real access-log concept; no
# smart-lock/mid-tenancy access-log integration exists to link to
# otherwise), not the full access-log the doc's Section 18 imagines.
# AC-36: a CANCELLATION/EARLY_TERMINATION claim code within
# BOOKING_AGREEMENT links to the occupancy's TerminationCase instead of a
# BookingChangeRequest -- the latter only ever covers DATE_CHANGE/
# TERM_INTERPRETATION-style requests, never an actual termination.
DISPUTE_CLAIM_SOURCE_RECORD_TYPES = (
    "HABITABILITY_INCIDENT", "BOOKING_CHANGE_REQUEST", "SUBLET_REQUEST", "OCCUPANCY_HANDOVER_EVENT", "TERMINATION_CASE",
)

# Section 6 confidence: RESOLVED means the forum resolver found a mapped
# authority class for this claim; LEGAL_REVIEW_REQUIRED means it fell
# through to fail-closed handling (AC-42) -- it is NOT a statement about the
# claim's merits.
DISPUTE_RESOLVER_CONFIDENCE = ("RESOLVED", "LEGAL_REVIEW_REQUIRED")

DISPUTE_FINANCIAL_HOLD_STATUSES = ("PROPOSED", "ACTIVE", "RELEASE_PENDING", "RELEASED", "CLOSED")

# Section 26: "Allowed for material new evidence, processing error, external
# decision, fraud finding, or other configured grounds." INTERNAL_REVIEW_REQUESTED
# is this MVP's own addition -- the ground crud/disputes.py:request_internal_review
# stamps when a *party* (not an admin) is the one who reopened the case by
# requesting review of a decided A0 claim.
DISPUTE_CASE_REOPEN_GROUNDS = (
    "MATERIAL_NEW_EVIDENCE", "PROCESSING_ERROR", "EXTERNAL_DECISION", "FRAUD_FINDING", "INTERNAL_REVIEW_REQUESTED", "OTHER",
)

# Section 12/23/25: queue-routing metadata only -- NOT an access-control
# layer. AdminUser.role only has admin/super_admin (models/admin_user.py)
# and Membership.role only has provider-side roles (models/membership.py);
# no Trust & Safety/Legal/Dispute Officer role exists anywhere in this
# codebase to enforce team membership against. assigned_team just tells an
# admin which queue a case belongs in -- see crud/disputes.py:
# _default_assigned_team for the (deterministic, already-computed-facts-only)
# auto-assignment rule.
DISPUTE_CASE_TEAMS = ("DISPUTE_OPERATIONS", "TRUST_AND_SAFETY", "LEGAL_COMPLIANCE", "FINANCE")


class DisputeResolutionCase(Base):
    """ZR-ENG-CLR-010 Section 4/22/23: the general-purpose Dispute Case --
    "top-level container linking the parties, booking, jurisdiction snapshot,
    claims... and outcomes". Deliberately named/tabled apart from
    finance.DisputeCase, which predates this spec and stays exactly as-is:
    that one is a narrow, working chargeback/reconciliation-adjacent record
    tightly wired into crud/finance.py's ledger-reversal logic for
    category=CHARGEBACK. This model is the real Section 10 shape -- one case,
    one-or-more independently resolvable DisputeResolutionClaim rows, each
    routed through its own authority class -- for every dispute type Section
    10 lists (deposit, condition, cancellation, sublet/occupancy, safety,
    platform-fee, etc.) except payment chargebacks, which keep using the
    existing mechanism (a PAYMENT/REFUND_PAYOUT claim here can still be
    opened for visibility/triage, but a chargeback's actual money-reversal
    stays finance.DisputeCase's job -- see Section 15's own "create/link
    external_payment_dispute" language, which this MVP treats as already
    satisfied by that existing object rather than duplicated here).

    occupancy_id/property_id are nullable because not every claim family is
    occupancy-anchored (e.g. a pure ZOIKO_SERVICE platform-fee complaint) --
    same nullability rationale finance.DisputeCase already uses for
    payment_id/occupancy_id."""

    __tablename__ = "dispute_resolution_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int | None] = mapped_column(ForeignKey("occupancies.id", ondelete="SET NULL"), nullable=True, index=True)
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id", ondelete="SET NULL"), nullable=True, index=True)
    # Exactly one of these two is set -- who opened the case (Section 9/10 intake).
    opened_by_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    opened_by_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="SET NULL"), nullable=True)
    severity: Mapped[str] = mapped_column(String(10), default="SEV-2")
    status: Mapped[str] = mapped_column(String(25), default="SUBMITTED")
    primary_claim_family: Mapped[str] = mapped_column(String(30), nullable=False)
    # Section 4 "External Proceeding" concept -- set True once any claim on
    # this case resolves to an authority class this Zoiko cannot decide
    # internally (A2+). See app/models/dispute_external_proceeding.py (Phase
    # 3) for the actual filing/decision-tracking object this flag points at.
    external_dependency_flag: Mapped[bool] = mapped_column(default=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    # AC-31: "A closed case can be reopened without deleting or altering its
    # prior closure event" -- closed_at/closed_by_admin_id above are NEVER
    # cleared by a reopen; these four columns are the separate, additive
    # reopen event. reopened_by_admin_id is null for a party-triggered
    # reopen (crud/disputes.py:request_internal_review) -- that one didn't
    # come from an admin action at all.
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    reopen_grounds: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reopen_note: Mapped[str] = mapped_column(String(1000), default="")
    # Section 12/23: routing/queue metadata only -- see DISPUTE_CASE_TEAMS's
    # own comment above for why this isn't an access-control layer.
    assigned_team: Mapped[str | None] = mapped_column(String(20), nullable=True)
    assigned_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    # QA-Q45: non-empty only when this case was force-closed by
    # crud/disputes.py:close_case's force_close_reason path while one or
    # more claims were still open, each genuinely stuck awaiting an
    # external scheme/tribunal with no ETA -- AC-24's normal "every claim
    # must be terminal" rule is the default; this is the documented,
    # narrow exception, not a way to skip resolution generally.
    partial_closure_reason: Mapped[str] = mapped_column(String(1000), default="")
    # AC-39: "All material state changes are idempotent and protected by
    # optimistic/version concurrency controls." Previously only
    # DisputeResolutionHold had this (the one object QA-Q42 names); this
    # extends the same SQLAlchemy version_id_col mechanism to case, claim,
    # settlement and external proceeding so a concurrent admin action on
    # any of them raises StaleDataError -> a clean 409 instead of silently
    # overwriting another admin's change. Must be declared before
    # __mapper_args__ below (SQLAlchemy's documented pattern).
    version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": version}

    occupancy: Mapped["Occupancy"] = relationship()
    property: Mapped["Property"] = relationship()
    opened_by_guest: Mapped["Guest"] = relationship()
    opened_by_party: Mapped["Party"] = relationship()
    closed_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[closed_by_admin_id])
    reopened_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[reopened_by_admin_id])
    assigned_admin: Mapped["AdminUser"] = relationship(foreign_keys=[assigned_admin_id])
    claims: Mapped[list["DisputeResolutionClaim"]] = relationship(back_populates="case", cascade="all, delete-orphan")


class DisputeResolutionClaim(Base):
    """ZR-ENG-CLR-010 Section 5/22/23: one discrete allegation/remedy request
    within a case, with its own authority, amount, status and outcome --
    "The case must not force all three into one authority or one outcome"
    (Section 5's deposit+fee+lockout example). authority_class/
    resolver_confidence are set once by dispute_forum_resolver.py at
    creation (and re-run if the claim is materially edited -- not yet
    supported in this MVP, so they are otherwise immutable)."""

    __tablename__ = "dispute_resolution_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_code: Mapped[str] = mapped_column(String(50), nullable=False)
    claim_family: Mapped[str] = mapped_column(String(30), nullable=False)
    claimant_role: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    requested_remedy: Mapped[str] = mapped_column(String(1000), default="")
    authority_class: Mapped[str | None] = mapped_column(String(2), nullable=True)
    resolver_confidence: Mapped[str] = mapped_column(String(25), default="LEGAL_REVIEW_REQUIRED")
    resolver_notes: Mapped[str] = mapped_column(String(1000), default="")
    # Section 7: the same jurisdiction pack identity resolver_notes' free
    # text already describes ("Resolved from jurisdiction '...' market
    # policy pack vN"), now also as real, queryable/joinable columns --
    # see dispute_forum_resolver.py's own ForumResolution docstring. Both
    # null whenever no market pack informed authority_class (unconfigured
    # jurisdiction, no occupancy to resolve one from, or a family/
    # safety-forced path that never consults one, e.g. ZOIKO_SERVICE).
    # SET NULL (never CASCADE) on pack deletion -- this is a frozen record
    # of what applied at decision time, not a live dependency.
    policy_pack_id: Mapped[int | None] = mapped_column(ForeignKey("market_policy_packs.id", ondelete="SET NULL"), nullable=True)
    policy_pack_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # AC-35/36/37/38 -- see DISPUTE_CLAIM_SOURCE_RECORD_TYPES above. Both
    # null for a family with no source-record concept (DEPOSIT,
    # ZOIKO_SERVICE, etc.) or when no matching record exists for this
    # claim's occupancy.
    source_record_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # AC-35 ("preserve Section 3 arrangement classification and consent
    # evidence")/AC-37 ("reference exact... proposal/version")/AC-38
    # ("preserve Section 9 possession and access evidence"): a frozen copy
    # of the specific fields each AC actually names from whichever record
    # source_record_type/source_record_id points at, captured once at
    # link time -- not a live join, so a later change to that record (a
    # booking-change proposal superseded, a sublet request re-decided)
    # never silently rewrites what this claim was actually opened against.
    # Empty for every claim with no source record. See
    # crud/disputes.py:_build_source_record_snapshot.
    source_record_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(25), default="OPEN")
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason_code: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    # AC-39 -- see DisputeResolutionCase.version's own comment above.
    version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": version}

    case: Mapped["DisputeResolutionCase"] = relationship(back_populates="claims")
    decided_by_admin: Mapped["AdminUser"] = relationship()
    holds: Mapped[list["DisputeResolutionHold"]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    decisions: Mapped[list["DisputeDecision"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", order_by="DisputeDecision.decided_at",
    )


class DisputeResolutionHold(Base):
    """ZR-ENG-CLR-010 Section 20/23: a claim+amount+currency-scoped financial
    hold with a mandatory `authority_basis` -- "never a finding of
    liability", "minimum necessary restraint". Deliberately a new table, not
    a reuse of finance.FinancialHold, which is a platform-wide reconciliation
    -exception queue keyed by (source_type, source_id) with no amount/
    currency/authority-basis scoping at all -- Section 10's shape is a
    genuinely different object. No ledger wiring exists yet in this phase:
    this is the queryable, auditable hold record itself, not an automatic
    payout/refund blocker (that integration is later-phase work, same
    "record it honestly, automate it later" line finance.FinancialHold's own
    docstring already draws)."""

    __tablename__ = "dispute_resolution_holds"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    authority_basis: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(15), default="PROPOSED")
    reason_code: Mapped[str] = mapped_column(String(50), default="")
    created_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # AC-39/QA-Q42: "Two Admins attempt competing hold/release; optimistic
    # concurrency prevents stale action." The first use of SQLAlchemy's
    # version_id_col mechanism in this codebase -- scoped to this one
    # object (the only one Q42 names), not retrofitted everywhere. Every
    # UPDATE auto-increments this column; a session holding a stale copy
    # gets a StaleDataError on commit instead of silently overwriting a
    # concurrent admin's change -- see crud/disputes.py's
    # approve_financial_hold/release_financial_hold/confirm_release_financial_hold,
    # which convert that into a clean 409. Must be declared before
    # __mapper_args__ below references it (SQLAlchemy's documented pattern).
    version: Mapped[int] = mapped_column(default=1)
    # Section 20 "Maker-checker: Mandatory above configured thresholds or
    # for safety/legal/manual override cases" -- see
    # crud/disputes.py:_requires_maker_checker. Below the trigger, a hold
    # still opens straight to ACTIVE and releases straight to RELEASED
    # (these four columns stay null) -- no regression for the common case.
    approved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_requested_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    release_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str] = mapped_column(String(500), default="")
    # AC-10: "Financial holds are... time/review bounded" -- every hold
    # created from this point on gets a real review_at, computed in
    # crud/disputes.py:open_financial_hold from either an explicit
    # review_at the case officer supplies or
    # settings.dispute_financial_hold_default_review_days (a reasonable
    # MVP default, not a verified legal figure -- same honesty as every
    # other fixed-window constant in this codebase). Nullable only so a
    # hold created before this column existed doesn't need a backfill;
    # nothing in this build auto-releases a hold past review_at -- see
    # crud/disputes.py:is_hold_overdue_for_review, which only flags it for
    # a human, the same "review date", not "auto-expiry", distinction
    # Section 20 itself draws.
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __mapper_args__ = {"version_id_col": version}

    claim: Mapped["DisputeResolutionClaim"] = relationship(back_populates="holds")
    created_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[created_by_admin_id])
    approved_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[approved_by_admin_id])
    release_requested_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[release_requested_by_admin_id])
