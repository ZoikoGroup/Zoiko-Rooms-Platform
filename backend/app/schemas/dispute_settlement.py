from datetime import datetime

from app.schemas.common import CamelModel


class DisputeSettlementCreate(CamelModel):
    claim_ids: list[int]
    terms_text: str
    amount: float | None = None
    currency: str = "INR"
    expires_at: datetime | None = None
    acknowledges_no_nonwaivable_waiver: bool = False


class DisputeSettlementRespond(CamelModel):
    action: str  # "ACCEPT" | "REJECT" | "COUNTER"
    counter_terms_text: str | None = None
    counter_amount: float | None = None
    counter_currency: str | None = None
    counter_expires_at: datetime | None = None


class DisputeSettlementRead(CamelModel):
    id: int
    case_id: int
    proposed_by_role: str
    proposed_by_guest_id: str | None
    proposed_by_party_id: int | None
    status: str
    terms_text: str
    amount: float | None
    currency: str
    terms_hash: str
    acknowledges_no_nonwaivable_waiver: bool
    offered_at: datetime
    expires_at: datetime | None
    responded_by_guest_id: str | None
    responded_by_party_id: int | None
    responded_at: datetime | None
    response_note: str
    effective_at: datetime | None
    supersedes_settlement_id: int | None
    claim_ids: list[int] = []
    created_at: datetime
    version: int = 1
    accepted_party_snapshot: dict = {}
