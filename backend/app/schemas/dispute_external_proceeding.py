from datetime import date, datetime

from app.schemas.common import CamelModel


class DisputeExternalProceedingCreate(CamelModel):
    authority_type: str
    authority_name: str = ""
    external_reference: str = ""
    claim_ids: list[int]
    filed_at: date | None = None


class DisputeExternalProceedingStatusUpdate(CamelModel):
    status: str  # "ACCEPTED" | "PENDING" | "DISMISSED" | "WITHDRAWN"


class DisputeExternalProceedingDecide(CamelModel):
    outcome: str  # "UPHELD" | "PARTLY_UPHELD" | "NOT_UPHELD" | "SETTLED"
    decision_date: date
    finality_state: str  # "FINAL" | "UNDER_REVIEW"
    outcome_evidence_id: int | None = None
    reason_code: str = ""


class DisputeExternalProceedingRead(CamelModel):
    id: int
    case_id: int
    authority_type: str
    authority_name: str
    external_reference: str
    status: str
    finality_state: str | None
    filed_at: date | None
    decision_date: date | None
    outcome_evidence_id: int | None
    outcome_summary: str
    filed_by_admin_id: int
    decided_by_admin_id: int | None
    claim_ids: list[int] = []
    created_at: datetime
    version: int = 1
    external_deadline_at: date | None = None
    filed_after_deadline: bool = False
