from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.party import party_id_for_room
from app.models.admin_user import AdminUser
from app.models.authority_record import AuthorityRecord
from app.models.room import Room
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
        evidence_ref=data.evidence_ref,
        status="pending",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


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
