from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.crud.party import party_id_for_room
from app.models.admin_user import AdminUser
from app.models.authority_record import AuthorityRecord
from app.models.room import Room
from app.models.user_account import UserAccount
from app.schemas.marketplace import AuthorityRecordCreate

AUTHORITY_VALIDITY_DAYS = 365


def list_authority_records(db: Session, room_id: int | None = None) -> list[AuthorityRecord]:
    query = select(AuthorityRecord).order_by(AuthorityRecord.id)
    if room_id is not None:
        query = query.where(AuthorityRecord.room_id == room_id)
    return list(db.scalars(query))


def get_authority_record(db: Session, authority_id: int) -> AuthorityRecord | None:
    return db.get(AuthorityRecord, authority_id)


def get_valid_authority_for_room(db: Session, room_id: int) -> AuthorityRecord | None:
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(AuthorityRecord)
        .where(
            AuthorityRecord.room_id == room_id,
            AuthorityRecord.status == "verified",
            (AuthorityRecord.expires_at.is_(None)) | (AuthorityRecord.expires_at > now),
        )
        .order_by(AuthorityRecord.id.desc())
    )


def submit_authority_record(db: Session, admin: AdminUser, room: Room, data: AuthorityRecordCreate) -> AuthorityRecord:
    """party_id is the room's actual owning party (not necessarily the calling
    admin's own -- a super_admin can submit this on a provider's behalf, same as
    the route's own assert_provider_access(db, admin, party_id_for_room(room))
    check already targets)."""
    record = AuthorityRecord(
        party_id=party_id_for_room(room),
        room_id=room.id,
        authority_type=data.authority_type,
        relationship_type=data.relationship_type,
        evidence_ref=data.evidence_ref,
        status="pending",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def declare_authority_record(
    db: Session, user: UserAccount, room: Room, *, relationship_type: str, evidence_ref: str,
) -> AuthorityRecord:
    """Host self-service submission -- the Lister, Property & Authority
    Verification wireframe's own missing piece: unlike
    property_compliance.py's declare_property_compliance_credential (which
    is invoked by an AdminUser via the legacy Membership-based provider
    system), a Host managing their own listing authenticates as a
    UserAccount (see api/routes/user_hosting.py) -- so this reuses that
    router's own ownership-check shape (user.party_id ==
    room.property.owner_party_id) rather than assert_provider_access, which
    only ever recognizes an AdminUser's Membership.

    Lands in the same 'pending' status submit_authority_record already
    uses (no new status introduced) -- an admin still verifies/rejects it
    through the existing, unchanged verify_authority_record/
    reject_authority_record workflow."""
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only submit authority evidence for your own room")

    record = AuthorityRecord(
        party_id=user.party_id,
        room_id=room.id,
        authority_type=relationship_type.lower(),
        relationship_type=relationship_type,
        evidence_ref=evidence_ref,
        status="pending",
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    log_audit_event(
        db, None, "authority_record.declare", "authority_record", str(record.id),
        reason=f"user:{user.id}; room={room.id}; relationship={relationship_type}",
    )
    db.commit()
    return record


def list_authority_records_for_room_owned_by(db: Session, user: UserAccount, room: Room) -> list[AuthorityRecord]:
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view authority records for your own room")
    return list(
        db.scalars(select(AuthorityRecord).where(AuthorityRecord.room_id == room.id).order_by(AuthorityRecord.id.desc()))
    )


def verify_authority_record(db: Session, record: AuthorityRecord, verifier: AdminUser) -> AuthorityRecord:
    now = datetime.now(timezone.utc)
    record.status = "verified"
    record.verified_at = now
    record.expires_at = now + timedelta(days=AUTHORITY_VALIDITY_DAYS)
    record.verifier_admin_id = verifier.id
    db.commit()
    db.refresh(record)
    return record


def reject_authority_record(db: Session, record: AuthorityRecord, verifier: AdminUser) -> AuthorityRecord:
    record.status = "failed"
    record.verifier_admin_id = verifier.id
    db.commit()
    db.refresh(record)
    return record


def revoke_authority_record(db: Session, record: AuthorityRecord, revoker: AdminUser) -> AuthorityRecord:
    """ZR-ENG-CLR-012 Section 13: 'Host authority credentials are... independently
    expirable/revocable.' reject_authority_record above only ever applies to a
    still-pending record; this is the counterpart for one already 'verified' --
    e.g. evidence later turns out to be fraudulent, or the underlying lease/
    ownership basis has since ended. get_valid_authority_for_room only ever
    matches status == 'verified', so this takes effect immediately, same as an
    expiry -- no separate gate change needed."""
    if record.status != "verified":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a verified authority record can be revoked")
    record.status = "revoked"
    record.verifier_admin_id = revoker.id
    db.commit()
    db.refresh(record)
    return record
