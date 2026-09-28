"""Lister, Property & Authority Verification wireframe: the "is this
property/address itself real and evidenced" claim -- deliberately a
separate model/module from PropertyComplianceCredential (per-jurisdiction
regulatory documents like gas safety/EPC/HMO license, resolved from
MarketPolicyPack) and from IdentityVerification (who the lister is).
Mirrors AuthorityRecord's own shape and crud/authority.py's own functions
closely -- same party_id+room_id scoping, same admin-decision workflow,
same audit pattern -- rather than inventing a new one."""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.admin_user import AdminUser
from app.models.property_verification import PropertyVerification
from app.models.room import Room
from app.models.user_account import UserAccount

logger = logging.getLogger("uvicorn.error")

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

    _run_ocr_identity_cross_check(db, record, user)

    db.commit()
    db.refresh(record)

    log_audit_event(
        db, None, "property_verification.declare", "property_verification", str(record.id),
        reason=f"user:{user.id}; room={room.id}",
    )
    db.commit()
    return record


def _run_ocr_identity_cross_check(db: Session, record: PropertyVerification, user: UserAccount) -> None:
    """Real, working auto-verify, deliberately simplified (a prior version
    required BOTH the property document and a fresh re-read of the old
    identity document to independently show the name -- dropped as
    unnecessary complexity): checks the property document ONCE for EITHER
    a name OR the document number already read and stored at
    identity-verification time (IdentityVerification.ocr_extracted_number
    -- reused directly, never re-OCR'd). Either signal alone is enough to
    auto-verify; neither is mandatory on its own.

    The name checked is IdentityVerification.extracted_name when present
    -- a REAL name parsed off the identity document's own passport MRZ
    line (services/document_ocr.py:extract_name_from_mrz), not a guess.
    Only when no MRZ name was parseable (any non-passport document, or a
    passport whose MRZ line didn't read cleanly) does this fall back to
    the account's typed full_name. Preferring the document's own real
    content over a self-typed profile field means this works correctly
    even when the account's display name doesn't happen to match --
    exactly the gap that motivated adding extracted_name in the first
    place.

    A genuine match auto-verifies through the same real
    verify_property_verification crud path a human admin's approval click
    uses -- no admin queue, no second review. Anything else reroutes to
    additional_evidence_required with the real, specific reason -- never a
    blind accept. Fails open (leaves the record "pending" for manual
    review) only for infra problems -- OCR unavailable, no verified
    identity to compare against, unreadable file -- never for a real bad
    result.

    Still requires the owner's identity to already be a REAL, verified
    credential (crud/identity_verification.py:get_valid_identity_credential
    -- the same "source of truth" lookup ZR-ENG-CLR-012 Section 24 tells
    every other identity-dependent gate to use): that's what makes the
    name and stored document number trustworthy details to check against,
    rather than unverified, self-typed values."""
    from app.core.property_verification_uploads import resolve_property_verification_document_path
    from app.crud.identity_verification import get_valid_identity_credential
    from app.crud.payment_provider import get_system_admin
    from app.models.identity_verification import IdentityVerification
    from app.services import document_ocr

    if not document_ocr.is_available():
        logger.warning(
            "property_verification #%s: OCR unavailable (tesseract not found) -- "
            "leaving record in 'pending' for manual review", record.id,
        )
        return
    if not record.document_file_path:
        return

    credential = get_valid_identity_credential(db, record.party_id)
    if credential is None or credential.source_identity_verification_id is None:
        logger.info(
            "property_verification #%s: no valid identity credential for party #%s yet -- "
            "leaving in 'pending' until owner's identity is verified", record.id, record.party_id,
        )
        return
    identity_record = db.get(IdentityVerification, credential.source_identity_verification_id)
    if identity_record is None:
        return

    name_to_check = identity_record.extracted_name or user.full_name

    try:
        property_bytes = resolve_property_verification_document_path(record.document_file_path).read_bytes()
        matched, matched_via, confidence, snippet = document_ocr.check_identity_details_in_document(
            property_bytes, full_name=name_to_check, document_number=identity_record.ocr_extracted_number,
        )
    except Exception:
        logger.exception(
            "property_verification #%s: OCR check raised -- leaving record in 'pending' for manual review",
            record.id,
        )
        return

    record.ocr_extracted_text = snippet
    record.ocr_confidence = confidence
    record.ocr_name_matched = matched if matched_via != "number" else None
    db.flush()

    threshold = document_ocr.NAME_MATCH_CONFIDENCE_THRESHOLD

    if matched and confidence >= threshold:
        matched_on = (
            f"your name ({name_to_check})" if matched_via == "name"
            else f"your identity document's number ({identity_record.ocr_extracted_number})"
        )
        note = (
            f"Auto-verified: automated scan found {matched_on} on this document, at {confidence:.0f}% OCR "
            f"confidence (minimum {threshold:.0f}%)."
        )
        verify_property_verification(db, record, get_system_admin(db), notes=note)
        return

    if confidence < threshold:
        reason = f"Automated scan couldn't clearly read this document (confidence {confidence:.0f}%, below the {threshold:.0f}% minimum)."
    else:
        reason = f"Automated scan: couldn't find your name ({name_to_check}) or your identity document's number on this document."

    record.status = "additional_evidence_required"
    record.verifier_notes = f"{reason} Please re-upload a clearer document that shows your name or ID number."


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
