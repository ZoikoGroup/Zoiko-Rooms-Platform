from datetime import datetime

from app.schemas.common import CamelModel


class HabitabilityIncidentCreate(CamelModel):
    """ZR-ENG-CLR-006 Section 9.1: severity must be one of
    models.habitability_incident.HABITABILITY_SEVERITIES (H0-H3)."""

    severity: str
    description: str = ""


class HabitabilityIncidentResolve(CamelModel):
    resolution_notes: str = ""


class HabitabilityCreditApply(CamelModel):
    """ZR-ENG-CLR-006 Section 9.1 H1: 'possible rent adjustment/credit' -- an
    admin-applied amount against a specific paid RENT obligation for this
    incident's own occupancy, never a computed abatement percentage (see
    models/habitability_incident.py's own field docstring)."""

    obligation_id: int
    amount: float
    reason: str


class HabitabilityIncidentRead(CamelModel):
    id: int
    room_id: int
    occupancy_id: int
    reported_by_guest_id: str | None
    reported_by_admin_id: int | None
    severity: str
    description: str
    status: str
    opened_at: datetime
    resolved_at: datetime | None
    resolved_by_admin_id: int | None
    resolution_notes: str
    credited_amount: float
    credited_refund_request_id: int | None
    credit_reason: str
    credited_at: datetime | None
