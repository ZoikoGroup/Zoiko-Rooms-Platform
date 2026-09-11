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
    created_at: datetime


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
    executed_by_admin_id: int | None
    executed_at: datetime | None
    line_items: list[RefundEntitlementLineItemRead] = []
