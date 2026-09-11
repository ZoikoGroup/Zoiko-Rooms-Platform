from datetime import datetime

from app.schemas.common import CamelModel


class HandoverEventCreate(CamelModel):
    evidence_ref: str = ""
    notes: str = ""


class HandoverEventRead(CamelModel):
    id: int
    occupancy_id: int
    event_type: str
    actor_kind: str
    actor_admin_id: int | None
    actor_user_id: int | None
    evidence_ref: str
    notes: str
    correlation_id: str
    created_at: datetime


class ActivationDecisionRead(CamelModel):
    id: int
    occupancy_id: int
    decision_version: int
    gate_rule_version: int
    outcome: str
    reason_codes: list[str]
    checks: dict
    trigger: str
    evaluating_admin_id: int | None
    correlation_id: str
    evaluated_at: datetime


class ActivationGateStatusRead(CamelModel):
    occupancy_id: int
    latest_decision: ActivationDecisionRead | None
    handover_events: list[HandoverEventRead]


class OccupancyTimelineRead(CamelModel):
    handover_events: list[HandoverEventRead]
    activation_decisions: list[ActivationDecisionRead]
