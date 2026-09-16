"""ZR-ENG-CLR-012 Section 14: Property Compliance Credential Registry.
Admin-issued (no live registry/provider integration in this MVP -- same
manual-review posture as occupancy_eligibility.py), evidenced and audited the
same as any other admin decision on this platform."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.crud.market_policy import resolve_market_policy
from app.crud.party import assert_provider_access
from app.models.admin_user import AdminUser
from app.models.property_compliance_credential import PropertyComplianceCredential
from app.models.room import Room
from app.schemas.verification import PropertyComplianceCredentialRead

# Section 31's own "Credential expiry backlog: no mandatory credential
# should expire silently" pairs with Section 14's EXPIRING state -- same
# lead time as verification_followups.py's reminder window, so "EXPIRING"
# in the UI and "about to get a reminder" mean the same thing.
EXPIRING_SOON_THRESHOLD_DAYS = 30


def to_property_compliance_credential_read(db: Session, credential: PropertyComplianceCredential) -> PropertyComplianceCredentialRead:
    display_status = credential.status
    if credential.status == "VALID" and credential.expires_at:
        now = datetime.now(timezone.utc)
        if credential.expires_at <= now:
            display_status = "EXPIRED"
        elif credential.expires_at <= now + timedelta(days=EXPIRING_SOON_THRESHOLD_DAYS):
            display_status = "EXPIRING"

    return PropertyComplianceCredentialRead(
        id=credential.id, room_id=credential.room_id, requirement_code=credential.requirement_code,
        status=display_status, issuer_source=credential.issuer_source, evidence_ref=credential.evidence_ref,
        method=credential.method, jurisdiction_code=credential.jurisdiction_code,
        valid_from=credential.valid_from, expires_at=credential.expires_at,
        revoked_at=credential.revoked_at, revoked_reason=credential.revoked_reason,
        created_at=credential.created_at, policy_pack_version=credential.policy_pack_version,
    )


def issue_property_compliance_credential(
    db: Session, admin: AdminUser, *, room_id: int, requirement_code: str, issuer_source: str = "",
    evidence_ref: str = "", method: str = "", jurisdiction_code: str = "", expires_at: datetime | None = None,
) -> PropertyComplianceCredential:
    policy_pack_version = None
    if jurisdiction_code:
        try:
            policy_pack_version = resolve_market_policy(db, jurisdiction_code).version
        except HTTPException:
            policy_pack_version = None

    credential = PropertyComplianceCredential(
        room_id=room_id, requirement_code=requirement_code, status="VALID", issuer_source=issuer_source,
        evidence_ref=evidence_ref, method=method, jurisdiction_code=jurisdiction_code,
        issued_by_admin_id=admin.id, expires_at=expires_at, policy_pack_version=policy_pack_version,
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)

    log_audit_event(
        db, admin, "property_compliance_credential.issue", "property_compliance_credential", str(credential.id),
        reason=f"room={room_id}; requirement={requirement_code}",
    )
    return credential


def declare_property_compliance_credential(
    db: Session, admin: AdminUser, *, room_id: int, requirement_code: str, evidence_ref: str, method: str = "",
) -> PropertyComplianceCredential:
    """ZR-ENG-CLR-012 AC-43/Section 14: the Host's own self-declaration --
    evidence submitted, status UNDER_REVIEW until an admin confirms it
    (Section 14's own name: 'Evidence received; verification pending').
    Authorized via the same Membership/assert_provider_access check
    leasing.py already uses for every other Host-scoped action, not a new
    auth mechanism. Deliberately never satisfies
    get_valid_property_compliance_credential (VALID only), so an
    UNDER_REVIEW row can never silently pass the gate."""
    room = db.get(Room, room_id)
    if not room:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    assert_provider_access(db, admin, room.property.owner_party_id)
    if not evidence_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Evidence reference is required to declare a compliance credential")

    credential = PropertyComplianceCredential(
        room_id=room_id, requirement_code=requirement_code, status="UNDER_REVIEW",
        evidence_ref=evidence_ref.strip(), method=method, issued_by_admin_id=admin.id,
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)

    log_audit_event(
        db, admin, "property_compliance_credential.declare", "property_compliance_credential", str(credential.id),
        reason=f"room={room_id}; requirement={requirement_code}",
    )
    return credential


def verify_declared_property_compliance_credential(
    db: Session, credential: PropertyComplianceCredential, admin: AdminUser, *,
    jurisdiction_code: str = "", expires_at: datetime | None = None,
) -> PropertyComplianceCredential:
    """UNDER_REVIEW -> VALID: an admin confirms the Host's self-declared
    evidence is genuine. Only UNDER_REVIEW can transition this way -- a
    VALID/EXPIRED/REVOKED/SUSPENDED credential is re-issued fresh, not
    re-verified."""
    if credential.status != "UNDER_REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only an UNDER_REVIEW credential can be verified (current status: {credential.status})")

    policy_pack_version = None
    if jurisdiction_code:
        try:
            policy_pack_version = resolve_market_policy(db, jurisdiction_code).version
        except HTTPException:
            policy_pack_version = None

    credential.status = "VALID"
    credential.valid_from = datetime.now(timezone.utc)
    credential.jurisdiction_code = jurisdiction_code or credential.jurisdiction_code
    credential.expires_at = expires_at
    credential.policy_pack_version = policy_pack_version or credential.policy_pack_version
    db.commit()
    db.refresh(credential)

    log_audit_event(
        db, admin, "property_compliance_credential.verify_declared", "property_compliance_credential", str(credential.id),
        reason="",
    )
    return credential


def get_property_compliance_credential_or_404(db: Session, credential_id: int) -> PropertyComplianceCredential:
    credential = db.get(PropertyComplianceCredential, credential_id)
    if not credential:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property compliance credential not found")
    return credential


def revoke_property_compliance_credential(
    db: Session, credential: PropertyComplianceCredential, admin: AdminUser, *, reason: str = "",
) -> PropertyComplianceCredential:
    if credential.status != "VALID":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a VALID credential can be revoked (current status: {credential.status})")
    now = datetime.now(timezone.utc)
    credential.status = "REVOKED"
    credential.revoked_at = now
    credential.revoked_reason = reason
    db.commit()
    db.refresh(credential)

    log_audit_event(
        db, admin, "property_compliance_credential.revoke", "property_compliance_credential", str(credential.id),
        reason=reason,
    )
    return credential


def suspend_property_compliance_credential(
    db: Session, credential: PropertyComplianceCredential, admin: AdminUser, *, reason: str = "",
) -> PropertyComplianceCredential:
    """Section 14: 'SUSPENDED || Temporary hold pending investigation. ||
    Quarantine affected listing/feature.' Unlike revoke (permanent), this
    is meant to be resumed once the investigation clears -- see
    resume_property_compliance_credential."""
    if credential.status != "VALID":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a VALID credential can be suspended (current status: {credential.status})")
    credential.status = "SUSPENDED"
    credential.suspended_at = datetime.now(timezone.utc)
    credential.suspended_reason = reason
    db.commit()
    db.refresh(credential)

    log_audit_event(
        db, admin, "property_compliance_credential.suspend", "property_compliance_credential", str(credential.id),
        reason=reason,
    )
    return credential


def resume_property_compliance_credential(
    db: Session, credential: PropertyComplianceCredential, admin: AdminUser,
) -> PropertyComplianceCredential:
    if credential.status != "SUSPENDED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a SUSPENDED credential can be resumed (current status: {credential.status})")
    credential.status = "VALID"
    credential.suspended_at = None
    credential.suspended_reason = ""
    db.commit()
    db.refresh(credential)

    log_audit_event(
        db, admin, "property_compliance_credential.resume", "property_compliance_credential", str(credential.id),
        reason="",
    )
    return credential


def get_valid_property_compliance_credential(db: Session, room_id: int, requirement_code: str) -> PropertyComplianceCredential | None:
    """ZR-ENG-CLR-012 Section 24 source-of-truth rule -- the one function
    jurisdiction_gates_pass should call; never re-derive VALID from a raw
    evidence upload."""
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(PropertyComplianceCredential).where(
            PropertyComplianceCredential.room_id == room_id,
            PropertyComplianceCredential.requirement_code == requirement_code,
            PropertyComplianceCredential.status == "VALID",
            (PropertyComplianceCredential.expires_at.is_(None)) | (PropertyComplianceCredential.expires_at > now),
        )
        .order_by(PropertyComplianceCredential.valid_from.desc())
    )


def list_property_compliance_credentials_for_room(db: Session, room_id: int) -> list[PropertyComplianceCredential]:
    return list(
        db.scalars(
            select(PropertyComplianceCredential)
            .where(PropertyComplianceCredential.room_id == room_id)
            .order_by(PropertyComplianceCredential.created_at.desc())
        )
    )
