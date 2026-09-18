from datetime import datetime

from app.schemas.common import CamelModel


class OccupancyEligibilityCheckCreate(CamelModel):
    party_id: int
    jurisdiction_code: str
    method: str
    share_code: str = ""


class OccupancyEligibilityDecisionCreate(CamelModel):
    result_status: str
    reason_note: str = ""
    evidence_ref: str = ""
    follow_up_days: int | None = None


# AC-18: "Each requirement records... sharing." These are fixed, code-
# enforced facts about who can see what -- not admin-editable data, since
# the actual disclosure boundary is already hardcoded into which routes
# exist (Host has zero routes into any of these domains; only admins with
# the right role do). Surfaced here so the API response documents that
# boundary rather than leaving it implicit.
OCCUPANCY_ELIGIBILITY_SHARING_SCOPE = "Visible to the renter (their own record) and admins. Never visible to the Host."
SCREENING_SHARING_SCOPE = "Decision visible to the renter; provider_result_summary and reasoning visible to admins only. Host sees neither by default."
PROPERTY_COMPLIANCE_SHARING_SCOPE = "Visible to admins and the property's own Host. Never exposed to renters as raw evidence -- only via the publish/booking gate outcome."


class OccupancyEligibilityCheckRead(CamelModel):
    id: int
    party_id: int
    jurisdiction_code: str
    method: str
    share_code: str
    evidence_ref: str
    status: str
    reason_note: str
    checked_by_admin_id: int | None = None
    checked_at: datetime | None = None
    follow_up_due_at: datetime | None = None
    created_at: datetime
    sharing_scope: str = OCCUPANCY_ELIGIBILITY_SHARING_SCOPE


class PropertyComplianceCredentialCreate(CamelModel):
    room_id: int
    requirement_code: str
    issuer_source: str = ""
    evidence_ref: str = ""
    method: str = ""
    jurisdiction_code: str = ""
    expires_at: datetime | None = None


class PropertyComplianceCredentialRevoke(CamelModel):
    reason: str = ""


class PropertyComplianceCredentialDeclare(CamelModel):
    room_id: int
    requirement_code: str
    evidence_ref: str
    method: str = ""


class PropertyComplianceCredentialVerifyDeclared(CamelModel):
    jurisdiction_code: str = ""
    expires_at: datetime | None = None


class PropertyComplianceCredentialRead(CamelModel):
    id: int
    room_id: int
    requirement_code: str
    status: str
    issuer_source: str
    evidence_ref: str
    method: str
    jurisdiction_code: str
    valid_from: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_reason: str
    created_at: datetime
    policy_pack_version: int | None = None
    sharing_scope: str = PROPERTY_COMPLIANCE_SHARING_SCOPE


class ScreeningCheckCreate(CamelModel):
    party_id: int
    jurisdiction_code: str
    check_type: str
    provider_name: str = ""
    permissible_purpose: str
    host_policy_criteria: str = ""


class ScreeningDecisionCreate(CamelModel):
    decision_status: str
    decision_reason: str = ""
    provider_result_summary: str = ""


class ScreeningDisputeCreate(CamelModel):
    dispute_reason: str


class ScreeningCheckRead(CamelModel):
    id: int
    party_id: int
    jurisdiction_code: str
    check_type: str
    provider_name: str
    permissible_purpose: str
    host_policy_criteria: str
    provider_result_summary: str
    decision_status: str
    decision_reason: str
    adverse_action_notice_sent_at: datetime | None = None
    reviewed_by_admin_id: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    policy_pack_version: int | None = None
    sharing_scope: str = SCREENING_SHARING_SCOPE
    dispute_reason: str = ""
    disputed_at: datetime | None = None


class RenterVerificationStatusItem(CamelModel):
    requirement_code: str
    status: str
    expires_at: datetime | None = None
    jurisdiction_code: str = ""
    explanation: str = ""
    sharing_scope: str = ""
    retention_note: str = ""
    alternative_method_note: str = ""


class RenterVerificationStatus(CamelModel):
    identity: RenterVerificationStatusItem
    occupancy_eligibility: list[RenterVerificationStatusItem]


class VerificationOperationalMetricsRead(CamelModel):
    """ZR-ENG-CLR-012 Section 31 -- see
    app/services/verification_operational_metrics.py for what each one
    measures and why. Rate/average fields are null (not zero) when there's
    no data to compute them from yet."""

    occupancy_eligibility_pending_count: int
    occupancy_eligibility_avg_turnaround_seconds: float | None
    screening_pending_count: int
    screening_avg_turnaround_seconds: float | None
    property_credentials_expiring_within_30_days: int
    evidence_artifacts_not_yet_swept_for_retention: int
    break_glass_grants_last_30_days: int
