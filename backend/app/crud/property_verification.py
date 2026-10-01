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
from app.services import geocoding

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
    verify_due_property_verifications(db, room_id=room_id)
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
    verify_due_property_verifications(db, room_id=room_id)
    return list(
        db.scalars(
            select(PropertyVerification).where(PropertyVerification.room_id == room_id).order_by(PropertyVerification.id.desc())
        )
    )


def declare_property_verification(
    db: Session, user: UserAccount, room: Room, *,
    evidence_ref: str, stored_filename: str, original_filename: str, content_type: str, file_size: int,
    sha256_hash: str | None = None,
) -> PropertyVerification:
    """Host self-service submission, scoped to a room the calling host's own
    party actually owns. No OCR: details are grabbed with regex from the PDF
    text layer and the typed evidence_ref (services/document_regex.py),
    stored on the row, and compared against the host's name and the room's
    property address. The property's address is also looked up on a map
    (services/geocoding.py). The row then waits AUTO_VERIFY_DELAY_SECONDS and
    is verified by verify_due_property_verifications -- but only when the
    address was found on the map (street/house level, in the region's
    country). A map miss, a duplicate of another host's upload, or readable
    text in which neither the name nor the address matches goes to a super
    admin instead."""
    if not user.party_id or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only submit property evidence for your own room")
    if not evidence_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Evidence reference is required")

    record = PropertyVerification(
        party_id=user.party_id, room_id=room.id, evidence_ref=evidence_ref.strip(),
        document_file_path=stored_filename, document_file_original_name=original_filename,
        document_file_content_type=content_type, document_file_size=file_size,
        document_sha256=sha256_hash, status="pending",
    )
    _extract_and_match(db, record, user, room)
    _geocode_property_address(record, room)
    db.add(record)
    db.flush()

    duplicate_of = _find_duplicate(db, record)
    mismatch = not (record.name_matched or record.address_matched) and (
        record.name_matched is False or record.address_matched is False
    )
    if duplicate_of is not None:
        record.verifier_notes = REVIEW_PENDING_NOTE
        _notify_review_needed(
            db, record, user,
            f"This document matches property verification #{duplicate_of.id} submitted for another host's room "
            f"-- review for reuse or fraud.",
        )
    elif record.geocode_status != geocoding.FOUND:
        record.verifier_notes = REVIEW_PENDING_NOTE
        _notify_review_needed(
            db, record, user,
            f"The property address could not be confirmed on the map ({_geocode_reason(record)}).",
        )
    elif mismatch:
        record.verifier_notes = REVIEW_PENDING_NOTE
        _notify_review_needed(
            db, record, user,
            f"Neither the owner name nor the property address on this document matched "
            f"(name found: {record.extracted_owner_name or 'none'}; address found: {record.extracted_address or 'none'}).",
        )
    else:
        record.verifier_notes = AUTO_VERIFY_PENDING_NOTE

    db.commit()
    db.refresh(record)

    log_audit_event(
        db, None, "property_verification.declare", "property_verification", str(record.id),
        reason=f"user:{user.id}; room={room.id}",
    )
    db.commit()
    return record


# How long an upload shows as "Verifying..." before it is auto-verified -- same
# as identity verification (crud/identity_verification.py). The frontend polls
# while a submission is pending.
AUTO_VERIFY_DELAY_SECONDS = 10
AUTO_VERIFY_PENDING_NOTE = "Verifying automatically."
REVIEW_PENDING_NOTE = "Under review by the Zoiko team."


def _extract_and_match(db: Session, record: PropertyVerification, user: UserAccount, room: Room) -> None:
    """Regex-only (no OCR) owner name / address / document number, plus
    whether the document mentions the host's name and this property's
    address. name_matched/address_matched stay None when there's no text."""
    from app.core.property_verification_uploads import resolve_property_verification_document_path
    from app.crud.identity_verification import get_verified_identity_for_party
    from app.services import document_regex

    try:
        document_bytes = resolve_property_verification_document_path(record.document_file_path).read_bytes()
    except (OSError, TypeError):
        document_bytes = b""
    details = document_regex.extract_property_details(
        document_bytes, record.document_file_content_type, record.evidence_ref,
    )
    record.extracted_owner_name = details["owner_name"]
    record.extracted_address = details["address"]
    record.extracted_document_number = details["document_number"]

    # Compare only against real document text, or a typed "Owner:"/"Address:"
    # line -- a bare typed label like "title deed" leaves the result unknown.
    has_document_text = bool(details["document_text"].strip())
    name_text = details["text"] if has_document_text or details["owner_name"] else ""
    address_text = details["text"] if has_document_text or details["address"] else ""

    identity = get_verified_identity_for_party(db, user.party_id)
    names = [n for n in (user.full_name, identity.extracted_name if identity else None) if n]
    name_results = [document_regex.name_matches(n, name_text) for n in names]
    record.name_matched = True if True in name_results else (False if False in name_results else None)
    prop = room.property
    record.address_matched = document_regex.address_matches(prop.address, prop.city, address_text)
    # The optional landmark is only a fallback for when the full address doesn't match.
    if record.address_matched is False and getattr(prop, "landmark", ""):
        record.address_matched = document_regex.address_matches(prop.landmark, prop.city, address_text) or False


def _geocode_property_address(record: PropertyVerification, room: Room) -> None:
    """Looks the room's property address up on a map and stores the result
    on the record. Never raises -- an unreachable provider is stored as
    UNAVAILABLE, which routes the submission to manual review."""
    prop = room.property
    result = geocoding.geocode_address(prop.address, prop.city, getattr(prop, "landmark", None), prop.jurisdiction_code)
    record.geocode_status = result.status
    record.geocode_provider = result.provider
    record.geocode_query = result.query[:500]
    record.geocode_formatted_address = result.formatted_address[:500]
    record.geocode_latitude = result.latitude
    record.geocode_longitude = result.longitude
    record.geocode_precision = result.precision
    record.geocode_country_code = result.country_code[:2]
    record.geocode_detail = result.detail[:500]
    record.geocoded_at = datetime.now(timezone.utc)


def _geocode_reason(record: PropertyVerification) -> str:
    return record.geocode_detail or {
        geocoding.NOT_FOUND: "address not found",
        geocoding.IMPRECISE: "only the area was found, not the street or building",
        geocoding.COUNTRY_MISMATCH: "address is in a different country than the property's region",
        geocoding.UNAVAILABLE: "map lookup was unavailable",
    }.get(record.geocode_status or "", "address was not checked")


def _find_duplicate(db: Session, record: PropertyVerification) -> PropertyVerification | None:
    if not record.document_sha256:
        return None
    return db.scalar(
        select(PropertyVerification).where(
            PropertyVerification.document_sha256 == record.document_sha256,
            PropertyVerification.party_id != record.party_id,
            PropertyVerification.id != record.id,
        ).order_by(PropertyVerification.id)
    )


def _notify_review_needed(db: Session, record: PropertyVerification, user: UserAccount, reason: str) -> None:
    from app.crud import notification as notif_crud

    notif_crud.notify_all_super_admins(
        db,
        title="Property verification needs review",
        message=f"{user.full_name}'s property document (room #{record.room_id}) needs a manual check. {reason}",
        notification_type="property_verification.review_needed",
        related_entity_type="property_verification", related_entity_id=str(record.id),
    )
    notif_crud.notify_user(
        db, user.id,
        title="Property verification under review",
        message="Your property document is being checked by the Zoiko team. We'll let you know once it's done.",
        notification_type="property_verification.under_review",
        related_entity_type="property_verification", related_entity_id=str(record.id),
    )


def verify_due_property_verifications(db: Session, *, room_id: int | None = None) -> None:
    """Auto-verifies uploads that have waited AUTO_VERIFY_DELAY_SECONDS. Called
    whenever a room's property verifications are read, so there's no
    background job to lose on a restart -- the next read catches up."""
    query = select(PropertyVerification).where(
        PropertyVerification.status == "pending",
        PropertyVerification.verifier_notes == AUTO_VERIFY_PENDING_NOTE,
        # Never auto-verify an address that wasn't confirmed on the map.
        PropertyVerification.geocode_status == geocoding.FOUND,
    )
    if room_id is not None:
        query = query.where(PropertyVerification.room_id == room_id)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=AUTO_VERIFY_DELAY_SECONDS)
    due = [r for r in db.scalars(query) if _as_utc(r.created_at) <= cutoff]
    if not due:
        return

    from app.crud import notification as notif_crud
    from app.crud.payment_provider import get_system_admin

    try:
        system_admin = get_system_admin(db)
    except HTTPException:
        logger.warning("property auto-verify: no system admin configured -- leaving uploads in 'pending'")
        return
    for record in due:
        verify_property_verification(db, record, system_admin, notes=_auto_verified_note(record))
        notif_crud.notify_user_by_party(
            db, record.party_id,
            title="Property verified",
            message="Your property document has been verified.",
            notification_type="property_verification.verified",
            related_entity_type="property_verification", related_entity_id=str(record.id),
        )
        db.commit()


def _auto_verified_note(record: PropertyVerification) -> str:
    on_map = f"address found on the map ({record.geocode_precision.lower()} level)"
    matched = [label for label, ok in (("owner name", record.name_matched), ("property address", record.address_matched)) if ok]
    if matched:
        return f"Auto-verified: {on_map}; document matched the {' and '.join(matched)}."
    return f"Auto-verified: {on_map}; no readable document text to compare."


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


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
