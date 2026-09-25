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


def effective_verification_status(record, now: datetime) -> str:
    """Shared by user_verification.py's status summary and
    crud/rental_transaction_record.py: AuthorityRecord/PropertyVerification
    rows never get flipped to 'expired' in the background --
    get_valid_authority_for_room/get_valid_property_verification_for_room
    only check expires_at live, so a 'verified' row past its own expires_at
    stays stored as 'verified' forever. Reporting that raw value would
    misrepresent a lapsed claim as still current, so it's recomputed as
    'expired' instead. Any other stored status (pending/rejected/
    additional_evidence_required/revoked) is already accurate and passed
    through as-is. Duck-typed on .status/.expires_at so it works for both
    AuthorityRecord and PropertyVerification without importing either model
    here."""
    if record.status == "verified" and record.expires_at is not None and record.expires_at <= now:
        return "expired"
    return record.status


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


def declare_property_verification(
    db: Session, user: UserAccount, room: Room, *,
    evidence_ref: str, stored_filename: str, original_filename: str, content_type: str, file_size: int,
) -> PropertyVerification:
    """Host self-service submission, scoped to a room the calling host's own
    party actually owns -- same ownership-check shape as
    crud/authority.py:declare_authority_record and
    api/routes/user_hosting.py's own pattern. A real uploaded document is
    now required alongside evidence_ref -- previously evidence_ref (a free
    -text description) was the only thing ever recorded, with nothing
    actually evidencing the claim."""
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only submit property evidence for your own room")
    if not evidence_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Evidence reference is required")

    record = PropertyVerification(
        party_id=user.party_id, room_id=room.id, evidence_ref=evidence_ref.strip(),
        document_file_path=stored_filename, document_file_original_name=original_filename,
        document_file_content_type=content_type, document_file_size=file_size,
        status="pending",
    )
    db.add(record)
    db.flush()

    _run_ocr_address_check(db, record, room)

    db.commit()
    db.refresh(record)

    log_audit_event(
        db, None, "property_verification.declare", "property_verification", str(record.id),
        reason=f"user:{user.id}; room={room.id}",
    )
    db.commit()
    return record


def _run_ocr_address_check(db: Session, record: PropertyVerification, room: Room) -> None:
    """Mirrors crud/identity_verification.py's own OCR dispatch shape
    (_run_ocr_check/_run_identity_ocr_check) -- a genuine local OCR read of
    the uploaded document, checked against this room's own real
    Property.address/city (unlike identity verification, there IS a known-
    correct value to compare against here). A genuine match auto-verifies
    through the same real verify_property_verification crud path a human
    admin's approval click uses; anything else reroutes to
    additional_evidence_required with the real, specific reason -- never a
    blind accept. Fails open (leaves the record "pending" for manual
    review) only for infra problems -- OCR unavailable, unreadable file --
    never for a real bad result."""
    from app.core.property_verification_uploads import resolve_property_verification_document_path
    from app.crud.payment_provider import get_system_admin
    from app.services import document_ocr

    if not document_ocr.is_available():
        return
    if not record.document_file_path:
        return

    try:
        file_path = resolve_property_verification_document_path(record.document_file_path)
        matched, confidence, snippet = document_ocr.check_property_document_address(
            file_path.read_bytes(), room.property.address, room.property.city,
        )
    except Exception:
        return

    record.ocr_extracted_text = snippet
    record.ocr_confidence = confidence
    record.ocr_address_matched = matched
    db.flush()

    threshold = document_ocr.PROPERTY_ADDRESS_MATCH_CONFIDENCE_THRESHOLD

    if matched and confidence >= threshold:
        note = (
            f"Auto-verified: automated scan found content matching this property's registered address "
            f"({room.property.address}, {room.property.city}), at {confidence:.0f}% OCR confidence "
            f"(minimum {threshold:.0f}%)."
        )
        verify_property_verification(db, record, get_system_admin(db), notes=note)
        return

    if not matched:
        reason = (
            f"Automated scan couldn't find content matching this property's registered address "
            f"({room.property.address}, {room.property.city}) in this document."
        )
    else:
        reason = (
            f"Automated scan couldn't clearly read this document "
            f"(confidence {confidence:.0f}%, below the {threshold:.0f}% minimum)."
        )
    record.status = "additional_evidence_required"
    record.verifier_notes = f"{reason} Please re-upload a clearer document that shows the property's address."


def list_property_verifications_for_room_owned_by(db: Session, user: UserAccount, room: Room) -> list[PropertyVerification]:
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view property verifications for your own room")
    return list_property_verifications_for_room(db, room.id)


def verify_property_verification(
    db: Session, record: PropertyVerification, admin: AdminUser, *, notes: str = ""
) -> PropertyVerification:
    if record.status not in ("pending", "additional_evidence_required"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a pending property verification can be verified (current status: {record.status})")
    now = datetime.now(timezone.utc)
    record.status = "verified"
    record.verified_at = now
    record.expires_at = now + timedelta(days=PROPERTY_VERIFICATION_VALIDITY_DAYS)
    record.verifier_admin_id = admin.id
    record.verifier_notes = notes
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
