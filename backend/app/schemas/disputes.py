from datetime import datetime

from app.schemas.common import CamelModel


class DisputeClaimCreate(CamelModel):
    claim_code: str
    claim_family: str
    amount: float | None = None
    currency: str = "INR"
    requested_remedy: str = ""
    safety_flag: bool = False


class DisputeCaseCreate(CamelModel):
    occupancy_id: int | None = None
    claim: DisputeClaimCreate


class DisputeClaimRead(CamelModel):
    id: int
    case_id: int
    claim_code: str
    claim_family: str
    claimant_role: str
    amount: float | None
    currency: str
    requested_remedy: str
    authority_class: str | None
    resolver_confidence: str
    resolver_notes: str
    policy_pack_id: int | None = None
    policy_pack_version: int | None = None
    source_record_type: str | None = None
    source_record_id: str | None = None
    source_record_snapshot: dict = {}
    status: str
    outcome: str | None
    reason_code: str
    created_at: datetime
    decided_at: datetime | None
    decided_by_admin_id: int | None
    version: int = 1


class DisputeMoneyStatusByCurrency(CamelModel):
    currency: str
    amount_disputed: float
    amount_held: float
    amount_undisputed: float
    amount_settled: float


class DisputeCaseRead(CamelModel):
    id: int
    occupancy_id: int | None
    property_id: int | None
    opened_by_guest_id: str | None
    opened_by_party_id: int | None
    severity: str
    status: str
    primary_claim_family: str
    external_dependency_flag: bool
    opened_at: datetime
    closed_at: datetime | None
    reopened_at: datetime | None = None
    reopened_by_admin_id: int | None = None
    reopen_grounds: str | None = None
    reopen_note: str = ""
    assigned_team: str | None = None
    assigned_admin_id: int | None = None
    partial_closure_reason: str = ""
    version: int = 1
    money_status: list[DisputeMoneyStatusByCurrency] = []
    claims: list[DisputeClaimRead] = []


class DisputeClaimDecide(CamelModel):
    outcome: str  # "UPHELD" | "PARTLY_UPHELD" | "NOT_UPHELD"
    reason_code: str = ""
    # QA-Q51 authority matrix -- see
    # models/dispute_decision.py:DISPUTE_INTERNAL_DECISION_REASON_CATEGORIES.
    reason_category: str = "OTHER_SERVICE_REASON"


class DisputeCaseReopen(CamelModel):
    grounds: str  # "MATERIAL_NEW_EVIDENCE" | "PROCESSING_ERROR" | "EXTERNAL_DECISION" | "FRAUD_FINDING" | "OTHER"
    note: str = ""
    claim_ids: list[int] = []


class DisputeClaimReviewRequest(CamelModel):
    reason: str = ""


class DisputeCaseAssign(CamelModel):
    team: str | None = None
    assigned_admin_id: int | None = None


class DisputeCaseClose(CamelModel):
    # QA-Q45: non-empty only to force-close a case with one or more claims
    # still awaiting an open external proceeding -- see
    # crud/disputes.py:close_case's own precondition. Left blank, close
    # behaves exactly as before (every claim must already be terminal).
    force_close_reason: str = ""


class DisputeFinancialHoldCreate(CamelModel):
    amount: float
    currency: str = "INR"
    authority_basis: str
    reason_code: str = ""
    # AC-10: optional case-officer-supplied review date; falls back to
    # settings.dispute_financial_hold_default_review_days when omitted.
    review_at: datetime | None = None


class DisputeFinancialHoldRelease(CamelModel):
    release_reason: str = ""


class DisputeFinancialHoldRead(CamelModel):
    id: int
    claim_id: int
    amount: float
    currency: str
    authority_basis: str
    status: str
    version: int
    reason_code: str
    created_by_admin_id: int
    created_at: datetime
    approved_by_admin_id: int | None = None
    approved_at: datetime | None = None
    release_requested_by_admin_id: int | None = None
    release_requested_at: datetime | None = None
    released_at: datetime | None
    release_reason: str
    review_at: datetime | None = None
    is_overdue_for_review: bool = False
