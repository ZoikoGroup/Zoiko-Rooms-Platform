"""ZR-ENG-CLR-006 Section 5/19: the termination_case entity -- 'Every case
must have a termination_class, cause_code and initiator.' Renter- and
Host-initiated cases (Sections 7/8) share this one table/engine. Every
mandatory cause code from Section 5 is acceptable as a case's own cause_code
-- what varies is how its effective date gets resolved: auto-computed
(UNILATERAL_CAUSE_CODES/HOST_UNILATERAL_CAUSE_CODES), consent-gated
(MUTUAL_CAUSE_CODES), or a Super Admin's own manual decision
(PENDING_REVIEW, everything else -- see RENTER_ONLY_CAUSE_CODES/
HOST_ONLY_CAUSE_CODES below). The Jurisdiction Termination Policy Engine's
full multi-dimension resolver (Section 6) remains a later increment; today's
policy_snapshot only ever captures the one dimension this build resolves
(termination_notice_days)."""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-006 Section 5: the full mandatory global-core taxonomy, modeled
# completely (never trimmed at the data layer -- a case can always record
# its true cause) even though today's engine only knows how to resolve an
# effective date/liability outcome for a subset of them (see
# UNILATERAL_CAUSE_CODES). Anything else fails closed to manual review
# (AC-35) rather than fabricating a resolution.
TERMINATION_CAUSE_CODES = (
    "RENTER_ORDINARY_EARLY_EXIT",
    "RENTER_CONTRACT_BREAK",
    "RENTER_STATUTORY_RIGHT",
    "MUTUAL_SURRENDER",
    "ASSIGNMENT_OR_REPLACEMENT",
    "HOST_FAULT_OR_NONPERFORMANCE",
    "HOST_LAWFUL_POSSESSION_ACTION",
    "RENTER_BREACH",
    "PROPERTY_UNINHABITABLE",
    "CASUALTY_OR_FORCE_EVENT",
    "ABANDONMENT_REPORTED",
    "PLATFORM_SAFETY_INTERVENTION",
    "LEGAL_OR_REGULATORY_ORDER",
    "OTHER_COUNSEL_APPROVED",
)

# ZR-ENG-CLR-006 Section 7.1 Step 8: "Where unilateral right exists, status
# advances without needing Host approval." These are the only causes this
# increment lets a renter self-serve today -- every other mandatory cause
# code above either needs a capability this build doesn't have yet (a break-
# clause registry for RENTER_CONTRACT_BREAK, Section 3's own assignment flow
# for ASSIGNMENT_OR_REPLACEMENT) or is Host-/system-initiated, not
# renter-initiated (HOST_LAWFUL_POSSESSION_ACTION, RENTER_BREACH,
# PLATFORM_SAFETY_INTERVENTION, LEGAL_OR_REGULATORY_ORDER,
# OTHER_COUNSEL_APPROVED). ABANDONMENT_REPORTED needs a possession-
# confirmation workflow not built yet -- Section 4's own answer ("do not
# silently terminate the agreement") means it can never be auto-resolved
# the way an immediate/notice cause is, evidence or not. MUTUAL_SURRENDER is
# neither unilateral nor immediate -- see MUTUAL_CAUSE_CODES below for its
# own (consent-gated, not date-computed) pathway.
#
# NOTICE_CAUSE_CODES resolve an earliest_effective_date via the market
# pack's termination_notice_days (occupancy keeps running, and keeps
# accruing rent obligations exactly as before, through that date -- this
# is deliberately how this build avoids ever computing a fabricated
# "remaining months x rent" liability: unearned rent is just rent that was
# never generated past the effective date). IMMEDIATE_CAUSE_CODES are
# Host-fault/habitability/no-fault-event grounds where the spec requires
# zero renter liability and no notice period -- CASUALTY_OR_FORCE_EVENT
# (Section 5: "Fire, flood, disaster, authority closure or comparable event")
# belongs here for exactly the same reason PROPERTY_UNINHABITABLE does: it's
# nobody's fault, so neither party should face a notice-period charge for it,
# and either party may be the one to first report it (see
# HOST_UNILATERAL_CAUSE_CODES below for the Host-initiated mirror of this).
NOTICE_CAUSE_CODES = ("RENTER_ORDINARY_EARLY_EXIT",)
IMMEDIATE_CAUSE_CODES = ("HOST_FAULT_OR_NONPERFORMANCE", "PROPERTY_UNINHABITABLE", "CASUALTY_OR_FORCE_EVENT")
UNILATERAL_CAUSE_CODES = NOTICE_CAUSE_CODES + IMMEDIATE_CAUSE_CODES

# ZR-ENG-CLR-006 Section 21.1/QT-05: "Protected/statutory termination with
# zero renter liability" -- RENTER_STATUTORY_RIGHT can resolve immediately
# with zero liability (crud/termination.py:_resolve_case_opening) but ONLY
# when the renter has actually attached at least one evidence_refs entry
# (TerminationCase.evidence_refs) -- otherwise it falls to PENDING_REVIEW
# exactly as before (AC-35: fail closed, don't fabricate a resolution from
# an unsubstantiated claim). This is deliberately narrower than
# IMMEDIATE_CAUSE_CODES: those never need evidence to auto-resolve because
# their cause is either the Host's own admission (HOST_FAULT_OR_
# NONPERFORMANCE) or an objectively observable event (PROPERTY_UNINHABITABLE/
# CASUALTY_OR_FORCE_EVENT); a claimed statutory right is a legal assertion
# about the renter's own protected status, so it needs the renter to have
# actually supplied something before this build auto-grants the strongest
# refund/liability treatment.
EVIDENCE_GATED_CAUSE_CODES = ("RENTER_STATUTORY_RIGHT",)

# ZR-ENG-CLR-006 Section 7.1 Step 9 / Section 8.1 MUTUAL_SURRENDER_PROPOSAL:
# "Host and renter agree to end on an agreed date and terms" -- either party
# may propose it (crud/termination.py:open_termination_case/
# open_host_termination_case), but unlike NOTICE_CAUSE_CODES/
# IMMEDIATE_CAUSE_CODES the effective date is never computed from the market
# pack: it's whatever date the proposing party names, and it only becomes
# binding once the OTHER party affirmatively accepts (crud/termination.py:
# accept_mutual_surrender) -- "non-response follows the jurisdiction/contract
# rule, never an invented default" (Step 9), so this build deliberately has
# no auto-accept/auto-expire timeout; a pending proposal just stays pending
# until accepted or declined. Once accepted, Section 13's own fee table
# treats it exactly like any other effective_termination_date -- the
# existing Refund Entitlement Engine needs no change to handle it.
MUTUAL_CAUSE_CODES = ("MUTUAL_SURRENDER",)

# ZR-ENG-CLR-006 Section 8.1: the Host-initiated pathways table.
# HOST_NONPERFORMANCE, CASUALTY_OR_FORCE_EVENT and MUTUAL_SURRENDER_PROPOSAL
# are self-service today -- none of the first two need evidence, due process
# or jurisdiction ground resolution (the same immediate/zero-liability
# treatment IMMEDIATE_CAUSE_CODES already gives when a renter reports either
# one instead); MUTUAL_SURRENDER needs only the other party's own acceptance,
# not a jurisdiction ground.
HOST_UNILATERAL_CAUSE_CODES = ("HOST_FAULT_OR_NONPERFORMANCE", "CASUALTY_OR_FORCE_EVENT")

# ZR-ENG-CLR-006 AC-35: "An unsupported or ambiguous required legal pathway
# fails closed to authorized review" -- not "is rejected outright". Every
# cause code above that isn't already self-service (UNILATERAL_CAUSE_CODES/
# HOST_UNILATERAL_CAUSE_CODES/MUTUAL_CAUSE_CODES) can still open a case; it
# just lands in PENDING_REVIEW instead of an auto-resolved date, with no
# earliest_effective_date computed (this build has no evidence/due-process/
# jurisdiction-ground engine to compute one from -- see
# crud/termination.py:decide_termination_case, the Section 20.1
# POST .../decision endpoint, which is the only thing that can move a
# PENDING_REVIEW case forward). These two sets exist only to keep a party
# from opening a case under a cause code that, by its own Section 5
# definition, describes what the OTHER party does -- a renter cannot invoke
# "RENTER_BREACH" against themselves, and a Host cannot invoke a renter's own
# "RENTER_STATUTORY_RIGHT" on the renter's behalf. Every other recognized
# code is fair game for PENDING_REVIEW from either side.
RENTER_ONLY_CAUSE_CODES = ("RENTER_ORDINARY_EARLY_EXIT", "RENTER_CONTRACT_BREAK", "RENTER_STATUTORY_RIGHT", "ASSIGNMENT_OR_REPLACEMENT")
HOST_ONLY_CAUSE_CODES = ("HOST_LAWFUL_POSSESSION_ACTION", "RENTER_BREACH", "ABANDONMENT_REPORTED", "PLATFORM_SAFETY_INTERVENTION")

# ZR-ENG-CLR-006 Section 10: "notice_method... Portal, email, SMS, postal,
# personal service, prescribed form, external service, other permitted
# method." This build only ever serves notice one way -- the renter/Host
# submitting it through this platform's own API -- so PORTAL is the only
# value ever set today; the rest of the taxonomy is modeled (never trimmed
# at the data layer, same discipline as TERMINATION_CAUSE_CODES) for when a
# later increment adds an actual postal/SMS/external-service integration.
NOTICE_METHODS = ("PORTAL", "EMAIL", "SMS", "POSTAL", "PERSONAL_SERVICE", "PRESCRIBED_FORM", "EXTERNAL_SERVICE", "OTHER")

# ZR-ENG-CLR-006 Section 21.1: "Protected termination grounds such as
# domestic/family violence or health-related statutory pathways may involve
# highly sensitive evidence... avoid exposing sensitive reason details to
# the other party beyond what law requires." That description is Section
# 5's own RENTER_STATUTORY_RIGHT ("qualifying safety, domestic/family
# violence, military, care, disability, minimum-standard or other protected
# pathway") -- the only cause code this taxonomy defines as inherently
# sensitive. crud/termination.py:to_termination_case_read redacts `notes`
# for these when the viewer isn't a Super Admin or the case's own renter;
# the notification this case triggers (crud/termination.py:
# _notify_case_opened) already never included notes at all.
SENSITIVE_CAUSE_CODES = ("RENTER_STATUTORY_RIGHT",)

# ZR-ENG-CLR-006 Section 18.1's own state machine, trimmed to the states
# this increment actually drives -- CLASSIFIED/NOTICE_PENDING/MOVE_OUT_
# PENDING/DISPUTED/LEGAL_HOLD/COURT_OR_TRIBUNAL_PENDING belong to a later
# increment's contested/adjudicated pathways. SURRENDER_PROPOSED/
# SURRENDER_DECLINED are this build's own trimmed stand-in for that state
# machine's RESPONSE_OR_PROCESS_PENDING, scoped to MUTUAL_SURRENDER.
# PENDING_REVIEW/REJECTED_PATHWAY are the same stand-in for every other
# cause code this build can't auto-resolve (see RENTER_ONLY_CAUSE_CODES/
# HOST_ONLY_CAUSE_CODES above) -- a Super Admin's own decide_termination_case
# call is what moves PENDING_REVIEW to either EFFECTIVE_DATE_SET (approved,
# with the date and reason they set -- AC-29) or REJECTED_PATHWAY (declined,
# with a reason -- the pathway itself, e.g. an alleged statutory right, was
# determined not to apply).
TERMINATION_CASE_STATUSES = (
    "OPENED", "SURRENDER_PROPOSED", "SURRENDER_DECLINED", "PENDING_REVIEW", "REJECTED_PATHWAY",
    "EFFECTIVE_DATE_SET", "TERMINATED", "WITHDRAWN",
)


class TerminationCase(Base):
    """One post-commencement termination request. initiator_guest_id is set
    for a renter-initiated case (today, the only kind this table's crud
    layer creates) -- initiator_admin_id exists for the Host/Admin-initiated
    pathways a later increment adds, and is never both set alongside
    initiator_guest_id."""

    __tablename__ = "termination_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False, index=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False)
    initiator_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    initiator_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    cause_code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OPENED")
    notes: Mapped[str] = mapped_column(String(2000), default="")
    # ZR-ENG-CLR-006 Section 21.1/EVIDENCE_GATED_CAUSE_CODES above: opaque
    # references (document/upload IDs -- this build stores no file content
    # itself, same idiom as MitigationRecord.evidence_refs) a renter attaches
    # to substantiate a claimed protected/statutory ground. Empty for every
    # other cause code -- nothing here gates an ordinary/immediate/mutual
    # resolution, which never needed evidence in the first place.
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    # ZR-ENG-CLR-006 Section 10: notice_created_at is the immutable submission
    # timestamp; earliest_effective_date is the market-pack-resolved lawful
    # date for a NOTICE_/IMMEDIATE_CAUSE_CODES case (Section 6's
    # resolve_termination_policy, trimmed to this increment's notice-days-only
    # resolution), the proposed (not yet binding) date for a MUTUAL_CAUSE_
    # CODES case, or null for a PENDING_REVIEW case (nothing to compute it
    # from yet); effective_termination_date is the final date actually used,
    # set immediately for the first, once the other party accepts for the
    # second, or once a Super Admin decides for the third (still no
    # adjudicated_effective_date precedence path -- that's a later
    # increment).
    notice_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # ZR-ENG-CLR-006 Section 10: "notice_served_at -- when legally recognized
    # service occurred; may differ from creation time." For this build's only
    # real notice_method (PORTAL), submission IS service -- there's no
    # separate delivery step to wait on -- so it's set to notice_created_at
    # at the same moment, honestly reflecting that reality rather than
    # leaving it null or inventing a delay.
    notice_served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notice_method: Mapped[str] = mapped_column(String(30), default="PORTAL")
    # ZR-ENG-CLR-006 AC-02/Section 6's own CONTROL: "A refund calculation must
    # always be reproducible from the policy_snapshot_id, not from whatever
    # policy happens to be current when an auditor later opens the case."
    # The same crud/market_policy.py:to_policy_snapshot(...) dict already
    # persisted onto deposit_instruments.calculation_snapshot and
    # sublet_requests.policy_snapshot -- captured once, at case-open time,
    # never re-resolved live afterward.
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    earliest_effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ZR-ENG-CLR-006 Section 11.1 TRIBUNAL_OR_COURT_DETERMINED: "Amount
    # remains provisional until competent determination" -- this build has
    # no tribunal integration, so a Super Admin's own entry stands in for
    # that determination (crud/termination.py:set_tribunal_liability,
    # AC-29's own role-authorization + reason requirement), never a computed
    # formula. Read into calculate_refund_entitlement's own NOTICE_LIABILITY
    # line -- Section 12's refundable_total formula's own
    # "lawful_non_deposit_offsets" term.
    tribunal_liability_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0)
    tribunal_liability_reason: Mapped[str] = mapped_column(String(2000), default="")
    # ZR-ENG-CLR-006 Section 10: "adjudicated_effective_date -- Court/
    # tribunal/authority decision where relevant" -- takes precedence over
    # whatever earliest_effective_date/effective_termination_date this build
    # already computed or a party proposed (crud/termination.py:
    # set_adjudicated_effective_date, Super-Admin-only with a mandatory
    # reason -- AC-29 -- since this build has no real tribunal integration
    # to resolve it automatically, same doctrine as tribunal_liability_amount
    # above).
    adjudicated_effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    adjudicated_effective_date_reason: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    occupancy: Mapped["Occupancy"] = relationship()
    agreement: Mapped["Agreement"] = relationship()
    mitigation_records: Mapped[list["MitigationRecord"]] = relationship(back_populates="termination_case")
    decisions: Mapped[list["TerminationDecision"]] = relationship(
        back_populates="termination_case", order_by="TerminationDecision.decision_at",
    )


class MitigationRecord(Base):
    """ZR-ENG-CLR-006 Section 11.2/19: 'Where the applicable law or contract
    requires mitigation... the system must track availability/re-listing
    activity and replacement occupancy.' This build records that evidence,
    and -- when the case's resolved liability model is ACTUAL_REASONABLE_LOSS
    -- reads reasonable_reletting_costs/replacement_rent_amount back out to
    compute a real MITIGATION_CREDIT and enforce the overlap_guard doctrine
    ('Prevents collecting both replacement rent and the same period of lost
    rent from departing renter'); see crud/refund_entitlement.py:
    _compute_mitigation_credit. For every other liability model this ledger
    still only records evidence (Section 21.2 'Immutable evidence') --
    NOTICE_RENT/ZERO_LIABILITY/STATUTORY_BREAK_FEE/CAPPED_COMPENSATION don't
    charge a re-letting-cost-shaped liability for a credit to offset. One
    case can have several rows over time (first listed, later a replacement
    is found)."""

    __tablename__ = "mitigation_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    termination_case_id: Mapped[int] = mapped_column(ForeignKey("termination_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    marketed_for_reletting_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    listing_channels: Mapped[list] = mapped_column(JSON, default=list)
    replacement_booking_id: Mapped[int | None] = mapped_column(ForeignKey("occupancies.id", ondelete="SET NULL"), nullable=True)
    replacement_occupancy_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    replacement_rent_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    reasonable_reletting_costs: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(String(2000), default="")
    recorded_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    termination_case: Mapped["TerminationCase"] = relationship(back_populates="mitigation_records")


# ZR-ENG-CLR-006 Section 19's own termination_decision entity: how
# effective_termination_date actually got decided at a given point in a
# case's life. Distinct from TerminationCase's own flattened current-value
# columns (effective_termination_date/tribunal_liability_*/adjudicated_*),
# which only ever show the LATEST determination -- this is the queryable
# history of every determination, in the order they happened.
TERMINATION_DECISION_BASES = (
    "AUTO_RESOLVED_UNILATERAL",
    "AUTO_RESOLVED_EVIDENCE_GATED",
    "MUTUAL_SURRENDER_ACCEPTED",
    "PENDING_REVIEW_APPROVED",
    "PENDING_REVIEW_REJECTED",
    "ADJUDICATED",
)
TERMINATION_DECISION_AUTHORITIES = ("system", "renter", "host_admin", "super_admin")


class TerminationDecision(Base):
    """ZR-ENG-CLR-006 Section 19: 'case_id, effective_termination_at,
    decision_basis, authority, approved_by, decision_at, external_order_ref.'
    Appended (never updated in place) by crud/termination.py at every point
    an effective date/pathway outcome is actually decided: case-open
    auto-resolution, a PENDING_REVIEW decision, a mutual surrender
    acceptance, or a Super Admin's adjudicated date."""

    __tablename__ = "termination_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    termination_case_id: Mapped[int] = mapped_column(ForeignKey("termination_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    effective_termination_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    decision_basis: Mapped[str] = mapped_column(String(40), nullable=False)
    authority: Mapped[str] = mapped_column(String(20), nullable=False)
    approved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    external_order_ref: Mapped[str] = mapped_column(String(100), default="")
    reason: Mapped[str] = mapped_column(String(500), default="")

    termination_case: Mapped["TerminationCase"] = relationship(back_populates="decisions")
