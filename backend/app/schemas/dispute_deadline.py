from datetime import datetime

from app.schemas.common import CamelModel


class DisputeDeadlineCreate(CamelModel):
    deadline_type: str  # "PARTY_RESPONSE" | "EVIDENCE_CLOSE"
    due_at: datetime
    claim_id: int | None = None


class DisputeDeadlineExtend(CamelModel):
    new_due_at: datetime
    extension_basis: str


class DisputeDeadlineRead(CamelModel):
    id: int
    case_id: int
    claim_id: int | None
    deadline_type: str
    due_at: datetime
    original_due_at: datetime | None
    status: str
    extension_basis: str
    source: str
    created_by_admin_id: int | None
    created_at: datetime
    reminder_at: datetime | None = None
    is_overdue: bool = False
    is_reminder_due: bool = False
