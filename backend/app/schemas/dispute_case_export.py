from datetime import datetime

from app.schemas.common import CamelModel
from app.schemas.dispute_evidence import DisputeEvidenceRead
from app.schemas.disputes import DisputeCaseRead


class DisputeChronologyEvent(CamelModel):
    timestamp: datetime
    event_type: str
    summary: str


class DisputeCaseExportRead(CamelModel):
    case: DisputeCaseRead
    evidence_index: list[DisputeEvidenceRead]
    chronology: list[DisputeChronologyEvent]
    generated_at: datetime
    note: str
