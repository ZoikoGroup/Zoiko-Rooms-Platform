"""Lister, Property & Authority Verification wireframe: the "is this
property/address itself real and evidenced" claim -- deliberately a
separate model/module from PropertyComplianceCredential (per-jurisdiction
regulatory documents like gas safety/EPC/HMO license, resolved from
MarketPolicyPack) and from IdentityVerification (who the lister is).
Mirrors AuthorityRecord's own shape and crud/authority.py's own functions
closely -- same party_id+room_id scoping, same admin-decision workflow,
same audit pattern -- rather than inventing a new one."""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.admin_user import AdminUser
from app.models.property_verification import PropertyVerification
from app.models.room import Room
from app.models.user_account import UserAccount

PROPERTY_VERIFICATION_VALIDITY_DAYS = 365


def get_property_verification_or_404(db: Session, verification_id: int) -> PropertyVerification:
    record = db.get(PropertyVerification, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property verification not found")
    return record


def get_valid_property_verification_for_room(db: Session, room_id: int) -> PropertyVerification | None:
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(PropertyVerification)
        .where(
            PropertyVerification.room_id == room_id,
            PropertyVerification.status == "verified",
            (PropertyVerification.expires_at.is_(None)) | (PropertyVerification.expires_at > now),
        )
        .order_by(PropertyVerification.id.desc())
    )


def list_property_verifications_for_room(db: Session, room_id: int) -> list[PropertyVerification]:
    return list(
        db.scalars(
            select(PropertyVerification).where(PropertyVerification.room_id == room_id).order_by(PropertyVerification.id.desc())
        )
    )


def declare_property_verification(db: Session, user: UserAccount, room: Room, *, evidence_ref: str) -> PropertyVerification:
    """Host self-service submission, scoped to a room the calling host's own
    party actually owns -- same ownership-check shape as
    crud/authority.py:declare_authority_record and
    api/routes/user_hosting.py's own pattern."""
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only submit property evidence for your own room")
    if not evidence_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Evidence reference is required")

    record = PropertyVerification(
        party_id=user.party_id, room_id=room.id, evidence_ref=evidence_ref.strip(), status="pending",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    log_audit_event(
        db, None, "property_verification.declare", "property_verification", str(record.id),
        reason=f"user:{user.id}; room={room.id}",
    )
    db.commit()
    return record


def list_property_verifications_for_room_owned_by(db: Session, user: UserAccount, room: Room) -> list[PropertyVerification]:
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view property verifications for your own room")
    return list_property_verifications_for_room(db, room.id)


def verify_property_verification(db: Session, record: PropertyVerification, admin: AdminUser) -> PropertyVerification:
    if record.status not in ("pending", "additional_evidence_required"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a pending property verification can be verified (current status: {record.status})")
    now = datetime.now(timezone.utc)
    record.status = "verified"
    record.verified_at = now
    record.expires_at = now + timedelta(days=PROPERTY_VERIFICATION_VALIDITY_DAYS)
    record.verifier_admin_id = admin.id
    db.commit()
    db.refresh(record)
    return record


def reject_property_verification(db: Session, record: PropertyVerification, admin: AdminUser, *, notes: str = "") -> PropertyVerification:
    record.status = "rejected"
    record.verifier_admin_id = admin.id
    record.verifier_notes = notes
    db.commit()
    db.refresh(record)
    return record


def request_additional_property_evidence(db: Session, record: PropertyVerification, admin: AdminUser, *, notes: str = "") -> PropertyVerification:
    record.status = "additional_evidence_required"
    record.verifier_admin_id = admin.id
    record.verifier_notes = notes
    db.commit()
    db.refresh(record)
    return record


def revoke_property_verification(db: Session, record: PropertyVerification, admin: AdminUser, *, reason: str = "") -> PropertyVerification:
    """Same 'only a currently-verified record can be revoked' guard as
    crud/authority.py:revoke_authority_record -- a pending/rejected record
    has nothing live to take away."""
    if record.status != "verified":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a verified property verification can be revoked")
    record.status = "revoked"
    record.verifier_admin_id = admin.id
    record.verifier_notes = reason
    db.commit()
    db.refresh(record)
    return record
