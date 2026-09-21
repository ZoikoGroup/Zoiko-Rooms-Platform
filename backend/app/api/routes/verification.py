"""ZR-ENG-CLR-012: admin-facing Verification Operations routes. Manual-review
only in this MVP -- see crud/occupancy_eligibility.py."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud import occupancy_eligibility as crud
from app.crud import property_compliance as compliance_crud
from app.crud import property_verification as property_verification_crud
from app.crud import screening as screening_crud
from app.crud.audit import log_audit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.verification import (
    OccupancyEligibilityCheckCreate,
    OccupancyEligibilityCheckRead,
    OccupancyEligibilityDecisionCreate,
    PropertyComplianceCredentialCreate,
    PropertyComplianceCredentialDeclare,
    PropertyComplianceCredentialRead,
    PropertyComplianceCredentialRevoke,
    PropertyComplianceCredentialVerifyDeclared,
    PropertyVerificationRead,
    PropertyVerificationReject,
    PropertyVerificationRequestAdditionalEvidence,
    PropertyVerificationRevoke,
    ScreeningCheckCreate,
    ScreeningCheckRead,
    ScreeningDecisionCreate,
    ScreeningDisputeCreate,
    VerificationOperationalMetricsRead,
)
from app.services.evidence_retention import sweep_expired_evidence
from app.services.verification_followups import (
    sweep_identity_verification_follow_ups,
    sweep_occupancy_eligibility_follow_ups,
    sweep_property_compliance_follow_ups,
)
from app.services.verification_operational_metrics import compute_verification_operational_metrics

router = APIRouter(prefix="/api/verification", tags=["verification"], dependencies=[Depends(get_current_admin)])


@router.post("/occupancy-eligibility-checks", response_model=OccupancyEligibilityCheckRead, status_code=status.HTTP_201_CREATED)
def post_open_occupancy_eligibility_check(
    payload: OccupancyEligibilityCheckCreate, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    check = crud.open_occupancy_eligibility_check(
        db, admin, party_id=payload.party_id, jurisdiction_code=payload.jurisdiction_code,
        method=payload.method, share_code=payload.share_code,
    )
    return crud.to_occupancy_eligibility_check_read(db, check)


@router.get("/occupancy-eligibility-checks", response_model=list[OccupancyEligibilityCheckRead])
def get_pending_occupancy_eligibility_checks(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [crud.to_occupancy_eligibility_check_read(db, c) for c in crud.list_pending_occupancy_eligibility_checks(db)]


@router.get("/occupancy-eligibility-checks/party/{party_id}", response_model=list[OccupancyEligibilityCheckRead])
def get_occupancy_eligibility_checks_for_party(party_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [crud.to_occupancy_eligibility_check_read(db, c) for c in crud.list_occupancy_eligibility_checks_for_party(db, party_id)]


@router.post("/occupancy-eligibility-checks/{check_id}/decide", response_model=OccupancyEligibilityCheckRead)
def post_decide_occupancy_eligibility_check(
    check_id: int, payload: OccupancyEligibilityDecisionCreate,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    check = crud.get_occupancy_eligibility_check_or_404(db, check_id)
    updated = crud.record_occupancy_eligibility_result(
        db, check, admin, result_status=payload.result_status, reason_note=payload.reason_note,
        evidence_ref=payload.evidence_ref, follow_up_days=payload.follow_up_days,
    )
    return crud.to_occupancy_eligibility_check_read(db, updated)


@router.post(
    "/property-compliance-credentials", response_model=PropertyComplianceCredentialRead, status_code=status.HTTP_201_CREATED,
)
def post_issue_property_compliance_credential(
    payload: PropertyComplianceCredentialCreate, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    credential = compliance_crud.issue_property_compliance_credential(
        db, admin, room_id=payload.room_id, requirement_code=payload.requirement_code,
        issuer_source=payload.issuer_source, evidence_ref=payload.evidence_ref, method=payload.method,
        jurisdiction_code=payload.jurisdiction_code, expires_at=payload.expires_at,
    )
    return compliance_crud.to_property_compliance_credential_read(db, credential)


@router.get("/property-compliance-credentials/room/{room_id}", response_model=list[PropertyComplianceCredentialRead])
def get_property_compliance_credentials_for_room(room_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [
        compliance_crud.to_property_compliance_credential_read(db, c)
        for c in compliance_crud.list_property_compliance_credentials_for_room(db, room_id)
    ]


@router.post("/property-compliance-credentials/{credential_id}/revoke", response_model=PropertyComplianceCredentialRead)
def post_revoke_property_compliance_credential(
    credential_id: int, payload: PropertyComplianceCredentialRevoke,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    credential = compliance_crud.get_property_compliance_credential_or_404(db, credential_id)
    updated = compliance_crud.revoke_property_compliance_credential(db, credential, admin, reason=payload.reason)
    return compliance_crud.to_property_compliance_credential_read(db, updated)


@router.post(
    "/property-compliance-credentials/declare", response_model=PropertyComplianceCredentialRead,
    status_code=status.HTTP_201_CREATED,
)
def post_declare_property_compliance_credential(
    payload: PropertyComplianceCredentialDeclare, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-012 AC-43: a Host self-declares (status UNDER_REVIEW) --
    authorized via the room's own property owner Membership, same as every
    other Host-scoped action."""
    credential = compliance_crud.declare_property_compliance_credential(
        db, admin, room_id=payload.room_id, requirement_code=payload.requirement_code,
        evidence_ref=payload.evidence_ref, method=payload.method,
    )
    return compliance_crud.to_property_compliance_credential_read(db, credential)


@router.post(
    "/property-compliance-credentials/{credential_id}/verify-declared", response_model=PropertyComplianceCredentialRead,
    dependencies=[Depends(require_super_admin)],
)
def post_verify_declared_property_compliance_credential(
    credential_id: int, payload: PropertyComplianceCredentialVerifyDeclared,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    credential = compliance_crud.get_property_compliance_credential_or_404(db, credential_id)
    updated = compliance_crud.verify_declared_property_compliance_credential(
        db, credential, admin, jurisdiction_code=payload.jurisdiction_code, expires_at=payload.expires_at,
    )
    return compliance_crud.to_property_compliance_credential_read(db, updated)


@router.post("/property-compliance-credentials/{credential_id}/suspend", response_model=PropertyComplianceCredentialRead)
def post_suspend_property_compliance_credential(
    credential_id: int, payload: PropertyComplianceCredentialRevoke,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-012 Section 14: 'SUSPENDED -- Temporary hold pending
    investigation.' Reuses the revoke payload shape (just a reason)."""
    credential = compliance_crud.get_property_compliance_credential_or_404(db, credential_id)
    updated = compliance_crud.suspend_property_compliance_credential(db, credential, admin, reason=payload.reason)
    return compliance_crud.to_property_compliance_credential_read(db, updated)


@router.post("/property-compliance-credentials/{credential_id}/resume", response_model=PropertyComplianceCredentialRead)
def post_resume_property_compliance_credential(
    credential_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    credential = compliance_crud.get_property_compliance_credential_or_404(db, credential_id)
    updated = compliance_crud.resume_property_compliance_credential(db, credential, admin)
    return compliance_crud.to_property_compliance_credential_read(db, updated)


# --- Lister, Property & Authority Verification: admin review of Host-declared
# property evidence (separate model/module from property-compliance-credentials
# above -- see crud/property_verification.py's own docstring). ---


@router.get("/property-verifications/room/{room_id}", response_model=list[PropertyVerificationRead])
def get_property_verifications_for_room(room_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return property_verification_crud.list_property_verifications_for_room(db, room_id)


@router.post(
    "/property-verifications/{verification_id}/verify", response_model=PropertyVerificationRead,
    dependencies=[Depends(require_super_admin)],
)
def post_verify_property_verification(
    verification_id: int, request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    record = property_verification_crud.get_property_verification_or_404(db, verification_id)
    updated = property_verification_crud.verify_property_verification(db, record, admin)
    log_audit_event(db, admin, "property_verification.verify", "property_verification", str(verification_id), get_correlation_id(request))
    db.commit()
    return updated


@router.post(
    "/property-verifications/{verification_id}/reject", response_model=PropertyVerificationRead,
    dependencies=[Depends(require_super_admin)],
)
def post_reject_property_verification(
    verification_id: int, payload: PropertyVerificationReject, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    record = property_verification_crud.get_property_verification_or_404(db, verification_id)
    updated = property_verification_crud.reject_property_verification(db, record, admin, notes=payload.notes)
    log_audit_event(
        db, admin, "property_verification.reject", "property_verification", str(verification_id),
        get_correlation_id(request), reason=payload.notes,
    )
    db.commit()
    return updated


@router.post(
    "/property-verifications/{verification_id}/request-additional-evidence", response_model=PropertyVerificationRead,
    dependencies=[Depends(require_super_admin)],
)
def post_request_additional_property_evidence(
    verification_id: int, payload: PropertyVerificationRequestAdditionalEvidence, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    record = property_verification_crud.get_property_verification_or_404(db, verification_id)
    updated = property_verification_crud.request_additional_property_evidence(db, record, admin, notes=payload.notes)
    log_audit_event(
        db, admin, "property_verification.request_additional_evidence", "property_verification", str(verification_id),
        get_correlation_id(request), reason=payload.notes,
    )
    db.commit()
    return updated


@router.post(
    "/property-verifications/{verification_id}/revoke", response_model=PropertyVerificationRead,
    dependencies=[Depends(require_super_admin)],
)
def post_revoke_property_verification(
    verification_id: int, payload: PropertyVerificationRevoke, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    record = property_verification_crud.get_property_verification_or_404(db, verification_id)
    updated = property_verification_crud.revoke_property_verification(db, record, admin, reason=payload.reason)
    log_audit_event(
        db, admin, "property_verification.revoke", "property_verification", str(verification_id),
        get_correlation_id(request), reason=payload.reason,
    )
    db.commit()
    return updated


@router.post("/occupancy-eligibility-checks/sweep-follow-ups", dependencies=[Depends(require_super_admin)])
def post_sweep_occupancy_eligibility_follow_ups(admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    """ZR-ENG-CLR-012 Section 9/AC-31: manual substitute for a cron tick, same
    shape as leasing.py's offer/checkout expiry sweeps -- no scheduler exists
    in this stack."""
    notified = sweep_occupancy_eligibility_follow_ups(db)
    log_audit_event(
        db, admin, "occupancy_eligibility.follow_up_sweep", "occupancy_eligibility_check", "bulk",
        reason=f"notified {len(notified)} check(s)",
    )
    db.commit()
    return {"notifiedCount": len(notified), "checkIds": [c.id for c in notified]}


@router.post("/identity-verifications/sweep-follow-ups", dependencies=[Depends(require_super_admin)])
def post_sweep_identity_verification_follow_ups(admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    """ZR-ENG-CLR-012 AC-31 applied to identity credentials."""
    notified = sweep_identity_verification_follow_ups(db)
    log_audit_event(
        db, admin, "identity_verification.follow_up_sweep", "identity_verification", "bulk",
        reason=f"notified {len(notified)} record(s)",
    )
    db.commit()
    return {"notifiedCount": len(notified), "recordIds": [r.id for r in notified]}


@router.post("/property-compliance-credentials/sweep-follow-ups", dependencies=[Depends(require_super_admin)])
def post_sweep_property_compliance_follow_ups(admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    """ZR-ENG-CLR-012 AC-31 applied to property compliance credentials."""
    notified = sweep_property_compliance_follow_ups(db)
    log_audit_event(
        db, admin, "property_compliance_credential.follow_up_sweep", "property_compliance_credential", "bulk",
        reason=f"notified {len(notified)} credential(s)",
    )
    db.commit()
    return {"notifiedCount": len(notified), "credentialIds": [c.id for c in notified]}


@router.post("/evidence-artifacts/sweep-retention", dependencies=[Depends(require_super_admin)])
def post_sweep_expired_evidence(admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    """ZR-ENG-CLR-012 Section 28/AC-28/AC-29: manual substitute for a cron
    tick, same shape as leasing.py's offer/checkout expiry sweeps -- no
    scheduler exists in this stack."""
    deleted = sweep_expired_evidence(db)
    log_audit_event(
        db, admin, "evidence_artifact.retention_sweep", "evidence_artifact", "bulk",
        reason=f"deleted {len(deleted)} artifact(s)",
    )
    db.commit()
    return {"deletedCount": len(deleted), "artifactIds": [a.id for a in deleted]}


@router.get("/operational-metrics", response_model=VerificationOperationalMetricsRead)
def get_verification_operational_metrics(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    """ZR-ENG-CLR-012 Section 31 -- see
    app/services/verification_operational_metrics.py."""
    return compute_verification_operational_metrics(db)


@router.post("/screening-checks", response_model=ScreeningCheckRead, status_code=status.HTTP_201_CREATED)
def post_open_screening_check(
    payload: ScreeningCheckCreate, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    check = screening_crud.open_screening_check(
        db, admin, party_id=payload.party_id, jurisdiction_code=payload.jurisdiction_code,
        check_type=payload.check_type, permissible_purpose=payload.permissible_purpose,
        provider_name=payload.provider_name, host_policy_criteria=payload.host_policy_criteria,
    )
    return screening_crud.to_screening_check_read(check)


@router.get("/screening-checks", response_model=list[ScreeningCheckRead])
def get_pending_screening_checks(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [screening_crud.to_screening_check_read(c) for c in screening_crud.list_pending_screening_checks(db)]


@router.get("/screening-checks/party/{party_id}", response_model=list[ScreeningCheckRead])
def get_screening_checks_for_party(party_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [screening_crud.to_screening_check_read(c) for c in screening_crud.list_screening_checks_for_party(db, party_id)]


@router.post("/screening-checks/{check_id}/decide", response_model=ScreeningCheckRead)
def post_decide_screening_check(
    check_id: int, payload: ScreeningDecisionCreate, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    check = screening_crud.get_screening_check_or_404(db, check_id)
    updated = screening_crud.record_screening_decision(
        db, check, admin, decision_status=payload.decision_status, decision_reason=payload.decision_reason,
        provider_result_summary=payload.provider_result_summary,
    )
    return screening_crud.to_screening_check_read(updated)


@router.post("/screening-checks/{check_id}/dispute", response_model=ScreeningCheckRead)
def post_dispute_screening_check(
    check_id: int, payload: ScreeningDisputeCreate, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """AC-48: 'User challenge corrects inaccurate evidence; credential is
    re-evaluated/versioned.' Recorded by an admin on the applicant's behalf
    in this MVP -- no renter-facing screening UI exists yet."""
    check = screening_crud.get_screening_check_or_404(db, check_id)
    updated = screening_crud.dispute_screening_decision(db, check, admin, dispute_reason=payload.dispute_reason)
    return screening_crud.to_screening_check_read(updated)
