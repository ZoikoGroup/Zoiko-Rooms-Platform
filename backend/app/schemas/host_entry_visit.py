from datetime import datetime

from app.schemas.common import CamelModel


class HostEntryVisitSchedule(CamelModel):
    """Section 9 gap: purpose must be one of
    models.host_entry_visit.ENTRY_VISIT_PURPOSES. scheduled_at must be at
    least MarketPolicyPack.entry_notice_hours ahead of now, unless
    is_emergency is set -- in which case emergency_reason is required."""

    purpose: str = "INSPECTION"
    scheduled_at: datetime
    is_emergency: bool = False
    emergency_reason: str = ""
    notes: str = ""


class HostEntryVisitComplete(CamelModel):
    notes: str = ""


class HostEntryVisitRead(CamelModel):
    id: int
    occupancy_id: int
    room_id: int
    scheduled_by_admin_id: int
    purpose: str
    notes: str
    scheduled_at: datetime
    is_emergency: bool
    emergency_reason: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
