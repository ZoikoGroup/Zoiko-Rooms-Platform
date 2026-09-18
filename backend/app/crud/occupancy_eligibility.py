"""ZR-ENG-CLR-012 Section 9: Occupancy / Right-to-Rent / Eligibility
Verification. Manual-review only in this MVP -- there is no live government
registry integration, so every check is opened and decided by an admin
(Section 16's "Manual Verification Operations" verifier mode), evidenced and
audited the same as any other admin decision in this codebase."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.admin_user import AdminUser
from app.models.occupancy_eligibility_check import (
    OCCUPANCY_ELIGIBILITY_METHODS,
    OCCUPANCY_ELIGIBILITY_STATUSES,
    OCCUPANCY_ELIGIBILITY_TERMINAL_STATUSES,
    OccupancyEligibilityCheck,
)
from app.models.verification_credential import VerificationCredential
from app.schemas.verification import OccupancyEligibilityCheckRead


def _display_status(db: Session, check: OccupancyEligibilityCheck) -> str:
    """ZR-ENG-CLR-012 Section 8: EXPIRED is a real check state, but it's a
    computed fact (has the credential this PASS issued since lapsed?), not
    something record_occupancy_eligibility_result ever sets directly --
    Section 24's own source-of-truth rule says the credential is what's
    authoritative, so the check's displayed status defers to it rather than
    a second, potentially-stale copy of the same fact."""
    if check.status != "PASS":
        return check.status
    credential = get_valid_occupancy_eligibility_credential(db, check.party_id, check.jurisdiction_code)
    return "PASS" if credential else "EXPIRED"


def to_occupancy_eligibility_check_read(db: Session, check: OccupancyEligibilityCheck) -> OccupancyEligibilityCheckRead:
    return OccupancyEligibilityCheckRead(
        id=check.id, party_id=check.party_id, jurisdiction_code=check.jurisdiction_code, method=check.method,
        share_code=check.share_code, evidence_ref=check.evidence_ref, status=_display_status(db, check),
        reason_note=check.reason_note, checked_by_admin_id=check.checked_by_admin_id, checked_at=check.checked_at,
        follow_up_due_at=check.follow_up_due_at, created_at=check.created_at,
    )


def open_occupancy_eligibility_check(
    db: Session, admin: AdminUser, *, party_id: int, jurisdiction_code: str, method: str, share_code: str = "",
) -> OccupancyEligibilityCheck:
    if method not in OCCUPANCY_ELIGIBILITY_METHODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown method '{method}'")

    check = OccupancyEligibilityCheck(
        party_id=party_id, jurisdiction_code=jurisdiction_code, method=method, share_code=share_code,
    )
    db.add(check)
    db.commit()
    db.refresh(check)

    log_audit_event(
        db, admin, "occupancy_eligibility.open", "occupancy_eligibility_check", str(check.id),
        reason=f"jurisdiction={jurisdiction_code}; method={method}",
    )
    db.commit()
    return check


def get_occupancy_eligibility_check_or_404(db: Session, check_id: int) -> OccupancyEligibilityCheck:
    check = db.get(OccupancyEligibilityCheck, check_id)
    if not check:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy eligibility check not found")
    return check


def record_occupancy_eligibility_result(
    db: Session, check: OccupancyEligibilityCheck, admin: AdminUser, *,
    result_status: str, reason_note: str = "", evidence_ref: str = "", policy_pack_version: int | None = None,
    follow_up_days: int | None = None,
) -> OccupancyEligibilityCheck:
    """ZR-ENG-CLR-012 Section 24: PASS/WAIVED_POLICY issue a scoped
    VerificationCredential (never a bare status flip -- Section 2's 'no raw
    document/check becomes a permanent fact without a credential'
    doctrine). Every other outcome never touches the credential table at
    all -- an absent credential IS the "not satisfied" state gates check
    for, including for a policy-based waiver, which is why WAIVED_POLICY
    issues one too rather than requiring the gate to special-case it.

    AC-09/AC-10: only PASS/FAIL_INELIGIBLE/WAIVED_POLICY are terminal --
    Section 8's own "Check state" table has every other outcome (including
    the new TECHNICAL_ERROR/FRAUD_REVIEW/SUSPENDED) route to alternate/
    manual review, so a check left in any of those can still be re-decided
    once more evidence arrives."""
    if check.status in OCCUPANCY_ELIGIBILITY_TERMINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A {check.status} check is terminal and cannot be re-decided")
    if result_status not in OCCUPANCY_ELIGIBILITY_STATUSES or result_status == "IN_PROGRESS":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid result status '{result_status}'")

    # ZR-ENG-CLR-012 Section 9 (acceptance criteria): "Manual reviewer PASS
    # requires reason and policy snapshot." Applies to WAIVED_POLICY too --
    # it issues the exact same VerificationCredential as PASS below, just on
    # a different basis, so it needs the same accountability.
    if result_status in ("PASS", "WAIVED_POLICY"):
        if not reason_note.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"A reason is required to record a {result_status} decision")
        if policy_pack_version is None:
            from app.crud.market_policy import resolve_market_policy

            policy_pack_version = resolve_market_policy(db, check.jurisdiction_code).version

    now = datetime.now(timezone.utc)
    check.status = result_status
    check.reason_note = reason_note
    check.evidence_ref = evidence_ref
    check.checked_by_admin_id = admin.id
    check.checked_at = now
    if result_status in ("PASS", "WAIVED_POLICY") and follow_up_days:
        check.follow_up_due_at = now + timedelta(days=follow_up_days)
    db.commit()
    db.refresh(check)

    credential = None
    if result_status in ("PASS", "WAIVED_POLICY"):
        credential = VerificationCredential(
            party_id=check.party_id, requirement_code="OCCUPANCY_ELIGIBILITY", status="VALID",
            method=result_status if result_status == "WAIVED_POLICY" else check.method,
            jurisdiction_code=check.jurisdiction_code,
            policy_pack_version=policy_pack_version,
            source_occupancy_eligibility_check_id=check.id,
            expires_at=check.follow_up_due_at,
        )
        db.add(credential)
        db.commit()
        db.refresh(credential)

    log_audit_event(
        db, admin, "occupancy_eligibility.decide", "occupancy_eligibility_check", str(check.id),
        reason=f"{result_status}; {reason_note}"[:500],
    )
    db.commit()
    return check


def get_valid_occupancy_eligibility_credential(db: Session, party_id: int, jurisdiction_code: str) -> VerificationCredential | None:
    """The one function crud/eligibility.py's gate should call -- never
    re-derive PASS/FAIL from OccupancyEligibilityCheck rows directly
    (Section 24's source-of-truth rule)."""
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(VerificationCredential).where(
            VerificationCredential.party_id == party_id,
            VerificationCredential.requirement_code == "OCCUPANCY_ELIGIBILITY",
            VerificationCredential.jurisdiction_code == jurisdiction_code,
            VerificationCredential.status == "VALID",
            (VerificationCredential.expires_at.is_(None)) | (VerificationCredential.expires_at > now),
        )
        .order_by(VerificationCredential.valid_from.desc())
    )


def list_occupancy_eligibility_checks_for_party(db: Session, party_id: int) -> list[OccupancyEligibilityCheck]:
    return list(
        db.scalars(
            select(OccupancyEligibilityCheck)
            .where(OccupancyEligibilityCheck.party_id == party_id)
            .order_by(OccupancyEligibilityCheck.created_at.desc())
        )
    )


def list_pending_occupancy_eligibility_checks(db: Session) -> list[OccupancyEligibilityCheck]:
    """"Pending" here means "not yet terminal" -- IN_PROGRESS plus every
    routed-to-review outcome (INCONCLUSIVE, TECHNICAL_ERROR, FRAUD_REVIEW,
    SUSPENDED) all still need admin attention, matching
    OCCUPANCY_ELIGIBILITY_TERMINAL_STATUSES' own definition of "done"."""
    return list(
        db.scalars(
            select(OccupancyEligibilityCheck)
            .where(OccupancyEligibilityCheck.status.notin_(OCCUPANCY_ELIGIBILITY_TERMINAL_STATUSES))
            .order_by(OccupancyEligibilityCheck.created_at.asc())
        )
    )
