from datetime import datetime

from app.schemas.common import CamelModel


class DisputeDecisionRead(CamelModel):
    id: int
    claim_id: int
    case_id: int
    outcome: str
    decision_basis: str
    authority: str
    decided_by_admin_id: int | None
    reason_code: str
    reason_category: str = "OTHER_SERVICE_REASON"
    external_proceeding_id: int | None
    settlement_id: int | None
    decided_at: datetime
