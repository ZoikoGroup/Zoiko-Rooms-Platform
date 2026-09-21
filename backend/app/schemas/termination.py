from datetime import date, datetime

from app.schemas.common import CamelModel


class TerminationCaseCreate(CamelModel):
    """ZR-ENG-CLR-006 Section 7.1 Step 3/6: 'Renter selects a reason category
    ... submits notice/request with required structured data.' cause_code
    must be one of models.termination_case.TERMINATION_CAUSE_CODES; which
    resolution path it takes (auto-computed, consent-gated, or manual
    PENDING_REVIEW) depends on which of that module's cause-code sets it
    falls into -- see that module's own docstring."""

    cause_code: str
    notes: str = ""
    # ZR-ENG-CLR-006 Section 7.1 Step 9/Section 10 agreed_effective_date:
    # required (and only meaningful) when cause_code is MUTUAL_SURRENDER --
    # the date the proposing party is offering, not yet binding until the
    # other party accepts (crud/termination.py:accept_mutual_surrender).
    proposed_effective_date: date | None = None
    # ZR-ENG-CLR-006 Section 10: how this notice was served -- PORTAL (this
    # submission itself) covers every real case today; see models/
    # termination_case.py:NOTICE_METHODS for the rest of the taxonomy.
    notice_method: str = "PORTAL"
    # ZR-ENG-CLR-006 Section 21.1/models.termination_case.
    # EVIDENCE_GATED_CAUSE_CODES: only meaningful for RENTER_STATUTORY_RIGHT
    # today -- a non-empty list here is what lets that one cause code
    # auto-resolve immediately/zero-liability instead of falling to
    # PENDING_REVIEW (AC-35: fail closed on an unsubstantiated claim, never
    # reject it outright).
    evidence_refs: list[str] = []


class TerminationCasePreviewRequest(CamelModel):
    """ZR-ENG-CLR-006 Section 7.1 Step 5: 'System displays earliest valid
    termination date, estimated continuing rent/compensation range, deposit
    treatment disclaimer, and alternatives' -- BEFORE the renter commits to
    Step 6's actual submission. Mirrors TerminationCaseCreate's own
    resolution-relevant fields exactly (same cause_code/proposed_effective_date/
    evidence_refs) since crud/termination.py:preview_termination_case reuses
    the identical resolution logic open_termination_case does -- what the
    preview describes is exactly what submitting would produce, not a
    separate guess."""

    cause_code: str
    proposed_effective_date: date | None = None
    evidence_refs: list[str] = []


class TerminationCasePreviewRead(CamelModel):
    """Section 7.1 Step 5's own output, computed but never persisted --
    no case, notice or evidence record is created by a preview. Every
    estimated_* field is None when resolved_status is PENDING_REVIEW (no
    effective date exists yet to estimate from -- AC-35's own fail-closed
    doctrine applies just as much to an estimate as to a real calculation:
    this build never fabricates one it can't actually compute)."""

    cause_code: str
    resolved_status: str
    requires_host_consent: bool
    requires_evidence_to_resolve_now: bool
    earliest_effective_date: date | None
    estimated_earned_rent: float | None
    estimated_refundable_unearned_rent: float | None
    estimated_liability_amount: float | None
    estimated_liability_note: str
    estimated_mitigation_credit: float | None
    estimated_net_refund: float | None
    deposit_disclaimer: str
    alternatives_note: str


class TerminationCaseRead(CamelModel):
    id: int
    occupancy_id: int
    agreement_id: int
    initiator_guest_id: str | None
    initiator_admin_id: int | None
    cause_code: str
    status: str
    notes: str
    notice_created_at: datetime
    notice_served_at: datetime | None
    notice_method: str
    evidence_refs: list[str]
    # ZR-ENG-CLR-006 AC-02: the immutable market-pack snapshot resolved at
    # case-open time -- see models/termination_case.py's own field docstring.
    policy_snapshot: dict
    # Null for a PENDING_REVIEW case -- see models/termination_case.py's own
    # field docstring for exactly when each becomes set.
    earliest_effective_date: date | None
    effective_termination_date: date | None
    withdrawn_at: datetime | None
    # ZR-ENG-CLR-006 Section 11.1 TRIBUNAL_OR_COURT_DETERMINED -- a Super
    # Admin's own entry, never a computed formula (see models/
    # termination_case.py's own field docstring).
    tribunal_liability_amount: float
    tribunal_liability_reason: str
    # ZR-ENG-CLR-006 Section 10 adjudicated_effective_date -- takes
    # precedence over the computed/proposed date when set (see models/
    # termination_case.py's own field docstring).
    adjudicated_effective_date: date | None
    adjudicated_effective_date_reason: str
    notice_service_proof_ref: str
    notice_service_recorded_by_admin_id: int | None
    created_at: datetime


class TerminationNoticeServiceRecord(CamelModel):
    """Section 11 gap: a Host/Admin's own attestation that real-world
    delivery occurred for a non-PORTAL notice_method -- see
    models/termination_case.py's own field docstring for why this can't be
    verified automatically."""

    served_at: datetime
    proof_ref: str


class TerminationCaseTribunalLiability(CamelModel):
    """ZR-ENG-CLR-006 Section 11.1/20.1: sets the provisional amount a
    tribunal/court determination requires this renter to pay beyond ordinary
    earned rent -- a Super Admin's own entry standing in for a real
    determination this build has no integration for (AC-29: role
    authorization + mandatory reason)."""

    amount: float
    reason: str


class TerminationCaseDecision(CamelModel):
    """ZR-ENG-CLR-006 Section 20.1 POST /termination-cases/{id}/decision --
    the only thing that can move a PENDING_REVIEW case forward (AC-29:
    manual overrides require role authorization and a reason). approve=True
    requires effective_termination_date (the Super Admin's own determined
    lawful date -- this build computes nothing for these causes, see
    models/termination_case.py:RENTER_ONLY_CAUSE_CODES/HOST_ONLY_CAUSE_CODES'
    own docstring); approve=False needs only the reason the pathway was
    rejected."""

    approve: bool
    effective_termination_date: date | None = None
    reason: str


class AdjudicatedEffectiveDateSet(CamelModel):
    """ZR-ENG-CLR-006 Section 10 adjudicated_effective_date/Section 20.1:
    a Super Admin's own entry standing in for a real court/tribunal/
    authority decision this build has no integration for (AC-29: role
    authorization + mandatory reason) -- takes precedence over whatever
    date this build already computed or a party proposed."""

    effective_date: date
    reason: str


class MitigationRecordCreate(CamelModel):
    """ZR-ENG-CLR-006 Section 11.2/20.1 POST /termination-cases/{id}/
    mitigation: records re-listing/re-letting evidence. Every field is
    optional -- a single call might log only 'listed for re-letting today',
    a later one might add the replacement occupancy once found."""

    marketed_for_reletting_at: date | None = None
    listing_channels: list[str] = []
    replacement_booking_id: int | None = None
    replacement_occupancy_start: date | None = None
    replacement_rent_amount: float | None = None
    reasonable_reletting_costs: float | None = None
    evidence_refs: list[str] = []
    notes: str = ""


class MitigationRecordRead(CamelModel):
    id: int
    termination_case_id: int
    marketed_for_reletting_at: date | None
    listing_channels: list[str]
    replacement_booking_id: int | None
    replacement_occupancy_start: date | None
    replacement_rent_amount: float | None
    reasonable_reletting_costs: float | None
    evidence_refs: list[str]
    notes: str
    recorded_by_admin_id: int | None
    created_at: datetime


class TerminationDecisionRead(CamelModel):
    """ZR-ENG-CLR-006 Section 19's own termination_decision entity -- an
    append-only history of how effective_termination_date was actually
    decided over a case's life (crud/termination.py:_record_decision)."""

    id: int
    termination_case_id: int
    effective_termination_at: date | None
    decision_basis: str
    authority: str
    approved_by_admin_id: int | None
    decision_at: datetime
    external_order_ref: str
    reason: str


class RefundEntitlementLineItemRead(CamelModel):
    id: int
    type: str
    source_obligation_id: int | None
    period_due_date: date | None
    amount: float
    basis_note: str
    refund_request_id: int | None


class RefundEntitlementRead(CamelModel):
    """ZR-ENG-CLR-006 Section 12.2: the itemized calculation output.
    net_refund == gross_refundable in this build -- no renter-fee/tax/
    credit/mitigation adjustment exists yet to make them differ (see
    models/refund_entitlement.py)."""

    id: int
    termination_case_id: int
    version: int
    currency: str
    gross_refundable: float
    net_refund: float
    status: str
    calculated_by_admin_id: int | None
    calculated_at: datetime
    approved_by_admin_id: int | None
    approved_at: datetime | None
    executed_by_admin_id: int | None
    executed_at: datetime | None
    line_items: list[RefundEntitlementLineItemRead] = []
