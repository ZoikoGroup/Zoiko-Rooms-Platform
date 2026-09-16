"""ZR-ENG-CLR-012 Sections 10/11: Application/Affordability Evidence and
Screening/Consumer-Report Controls. Admin-recorded (no live credit-bureau/
consumer-report provider integration in this MVP -- same manual-review
posture as occupancy_eligibility.py and property_compliance.py), evidenced
and audited the same as any other admin decision on this platform.

Deliberately NOT gated into check_agreement_eligibility: Section 11's own
doctrine is that screening 'must be a separate optional capability, not
smuggled into identity verification' -- a Host may request it, but its
result is never a hard platform-wide booking gate the way OCCUPANCY_
ELIGIBILITY is. Downstream Host-facing decisioning (accept/decline an
application) reads this table directly if it chooses to."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.audit import log_audit_event
from app.crud.market_policy import resolve_market_policy
from app.models.admin_user import AdminUser
from app.models.screening_check import SCREENING_CHECK_STATUSES, SCREENING_CHECK_TERMINAL_STATUSES, ScreeningCheck
from app.schemas.verification import ScreeningCheckRead
from app.services.verification_requirements import is_screening_check_type_permitted


def to_screening_check_read(check: ScreeningCheck) -> ScreeningCheckRead:
    return ScreeningCheckRead(
        id=check.id, party_id=check.party_id, jurisdiction_code=check.jurisdiction_code, check_type=check.check_type,
        provider_name=check.provider_name, permissible_purpose=check.permissible_purpose,
        host_policy_criteria=check.host_policy_criteria, provider_result_summary=check.provider_result_summary,
        decision_status=check.decision_status, decision_reason=check.decision_reason,
        adverse_action_notice_sent_at=check.adverse_action_notice_sent_at,
        reviewed_by_admin_id=check.reviewed_by_admin_id, reviewed_at=check.reviewed_at, created_at=check.created_at,
        policy_pack_version=check.policy_pack_version,
        dispute_reason=check.dispute_reason, disputed_at=check.disputed_at,
    )


def _resolve_policy_pack_version(db: Session, jurisdiction_code: str) -> int | None:
    try:
        return resolve_market_policy(db, jurisdiction_code).version
    except HTTPException:
        return None


def open_screening_check(
    db: Session, admin: AdminUser, *, party_id: int, jurisdiction_code: str, check_type: str,
    permissible_purpose: str, provider_name: str = "", host_policy_criteria: str = "",
) -> ScreeningCheck:
    if not permissible_purpose.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A permissible purpose must be recorded before opening a screening check")
    if not is_screening_check_type_permitted(db, jurisdiction_code, check_type):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Check type '{check_type}' is not permitted in jurisdiction '{jurisdiction_code}'")

    check = ScreeningCheck(
        party_id=party_id, jurisdiction_code=jurisdiction_code, check_type=check_type,
        provider_name=provider_name, permissible_purpose=permissible_purpose, host_policy_criteria=host_policy_criteria,
        policy_pack_version=_resolve_policy_pack_version(db, jurisdiction_code),
    )
    db.add(check)
    db.commit()
    db.refresh(check)

    log_audit_event(
        db, admin, "screening_check.open", "screening_check", str(check.id),
        reason=f"jurisdiction={jurisdiction_code}; check_type={check_type}",
    )
    return check


def get_screening_check_or_404(db: Session, check_id: int) -> ScreeningCheck:
    check = db.get(ScreeningCheck, check_id)
    if not check:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screening check not found")
    return check


def record_screening_decision(
    db: Session, check: ScreeningCheck, admin: AdminUser, *,
    decision_status: str, decision_reason: str = "", provider_result_summary: str = "",
) -> ScreeningCheck:
    """Section 11: 'A provider score is not a legal verdict' --
    provider_result_summary is recorded as the raw signal; decision_status
    is the separate human decision, never auto-derived from it.

    AC-09/AC-10: INCONCLUSIVE is deliberately not terminal -- it means
    "route to alternate/manual review", and DISPUTED_SOURCE (AC-48: "User
    challenge corrects inaccurate evidence; credential is re-evaluated")
    is exactly as re-decidable, by the same logic. PASS/FAIL are the only
    genuinely terminal outcomes."""
    if check.decision_status in SCREENING_CHECK_TERMINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A {check.decision_status} check is terminal and cannot be re-decided")
    if decision_status not in SCREENING_CHECK_STATUSES or decision_status in ("AUTHORIZED", "DISPUTED_SOURCE"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid decision status '{decision_status}'")

    now = datetime.now(timezone.utc)
    check.decision_status = decision_status
    check.decision_reason = decision_reason
    check.provider_result_summary = provider_result_summary
    check.reviewed_by_admin_id = admin.id
    check.reviewed_at = now

    # AC-35: "Adverse screening decisions can trigger required provider/
    # source and dispute-right communications." The actual jurisdiction-
    # mandated adverse-action notice content is Legal/Compliance-owned (not
    # fabricated here); this raises the event and records that it fired so
    # ops can confirm the real notice went out. See Notification content.
    if decision_status == "FAIL":
        notif_crud.notify_user_by_party(
            db, check.party_id,
            title="A screening decision has been made about your application",
            message=(
                "A decision on your application was influenced by a screening check. "
                "You have the right to review and dispute inaccurate information used in that check."
            ),
            notification_type="screening_check.adverse_decision",
            related_entity_type="screening_check", related_entity_id=str(check.id),
        )
        check.adverse_action_notice_sent_at = now

    db.commit()
    db.refresh(check)

    log_audit_event(
        db, admin, "screening_check.decide", "screening_check", str(check.id),
        reason=f"{decision_status}; {decision_reason}"[:500],
    )
    return check


def dispute_screening_decision(
    db: Session, check: ScreeningCheck, admin: AdminUser, *, dispute_reason: str,
) -> ScreeningCheck:
    """AC-48 (QA pack): 'User challenge corrects inaccurate evidence;
    credential is re-evaluated/versioned.' Only a DECIDED (PASS/FAIL)
    outcome can be disputed -- disputing an already-open check doesn't mean
    anything. Recorded by an admin on the applicant's behalf in this MVP
    (no renter-facing screening UI exists yet), same manual posture as the
    rest of this domain."""
    if check.decision_status not in SCREENING_CHECK_TERMINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a decided (PASS/FAIL) check can be disputed (current status: {check.decision_status})")
    if not dispute_reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A dispute reason is required")

    check.decision_status = "DISPUTED_SOURCE"
    check.dispute_reason = dispute_reason.strip()
    check.disputed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(check)

    log_audit_event(
        db, admin, "screening_check.dispute", "screening_check", str(check.id),
        reason=dispute_reason.strip()[:500],
    )
    return check


def list_screening_checks_for_party(db: Session, party_id: int) -> list[ScreeningCheck]:
    return list(
        db.scalars(
            select(ScreeningCheck).where(ScreeningCheck.party_id == party_id).order_by(ScreeningCheck.created_at.desc())
        )
    )


def list_pending_screening_checks(db: Session) -> list[ScreeningCheck]:
    """"Pending" here means "not yet terminal" -- AUTHORIZED, INCONCLUSIVE
    and DISPUTED_SOURCE all still need admin attention."""
    return list(
        db.scalars(
            select(ScreeningCheck)
            .where(ScreeningCheck.decision_status.notin_(SCREENING_CHECK_TERMINAL_STATUSES))
            .order_by(ScreeningCheck.created_at.asc())
        )
    )
