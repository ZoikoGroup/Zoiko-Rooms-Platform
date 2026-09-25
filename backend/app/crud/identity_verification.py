from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.mailer import (
    send_identity_verification_additional_evidence_email,
    send_identity_verification_approved_email,
    send_identity_verification_rejected_email,
)
from app.crud import notification as notif_crud
from app.crud.party import get_or_create_default_party
from app.models.admin_user import AdminUser
from app.models.identity_verification import DOCUMENT_CATEGORY_BY_TYPE, IdentityVerification, DOCUMENT_TYPES, IDENTITY_STATUSES
from app.models.party import Party
from app.models.user_account import UserAccount
from app.models.verification_credential import VerificationCredential
from app.schemas.marketplace import IdentityVerificationCreate

IDENTITY_VERIFICATION_VALIDITY_DAYS = 365


def list_identity_verifications(
    db: Session,
    admin: AdminUser,
    party_id: int | None = None,
    status: str | None = None,
) -> list[IdentityVerification]:
    query = select(IdentityVerification).order_by(IdentityVerification.id.desc())
    if admin.role != "super_admin":
        party = get_or_create_default_party(db, admin)
        query = query.where(IdentityVerification.party_id == party.id)
    elif party_id is not None:
        query = query.where(IdentityVerification.party_id == party_id)
    if status == NEEDS_REVIEW_FILTER:
        return _needs_review(list(db.scalars(query)))
    if status is not None:
        query = query.where(IdentityVerification.status == status)
    return list(db.scalars(query))


# Pseudo-status for the admin review queue: everything a super admin should
# look at, not just rows literally in "pending".
NEEDS_REVIEW_FILTER = "needs_review"


def _needs_review(records: list[IdentityVerification]) -> list[IdentityVerification]:
    """Pending submissions, plus each party's latest scan-flagged submission
    per document category -- the automated OCR check can wrongly reject a
    genuine document, and without this a super admin never sees it at all.
    Older flagged uploads the user has since replaced, and flagged uploads
    for a category the party already has verified, are left out so the
    queue shows one actionable row per person rather than every retry.
    `records` is newest-first (list_identity_verifications' ordering)."""
    verified_categories = {(r.party_id, r.document_category) for r in records if r.status == "verified"}
    latest_seen: set[tuple[int, str]] = set()
    queue = []
    for record in records:
        key = (record.party_id, record.document_category)
        is_latest = key not in latest_seen
        latest_seen.add(key)
        if record.status == "pending":
            queue.append(record)
        elif record.auto_flagged and is_latest and key not in verified_categories:
            queue.append(record)
    return queue


def get_identity_verification(db: Session, verification_id: int) -> IdentityVerification | None:
    return db.get(IdentityVerification, verification_id)


def submit_identity_verification(db: Session, admin: AdminUser, data: IdentityVerificationCreate) -> IdentityVerification:
    if data.document_type not in DOCUMENT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid document type")
    if not data.encrypted_reference:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Encrypted reference is required")

    if data.party_id is not None:
        if admin.role != "super_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins may create verification records for another party")
        party = db.get(Party, data.party_id)
        if not party:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")
    else:
        party = get_or_create_default_party(db, admin)

    record = IdentityVerification(
        party_id=party.id,
        document_type=data.document_type,
        document_category=DOCUMENT_CATEGORY_BY_TYPE.get(data.document_type, "identity"),
        encrypted_reference=data.encrypted_reference,
        evidence_ref=data.evidence_ref,
        status="pending",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _user_for_party(db: Session, party_id: int) -> UserAccount | None:
    return db.scalar(select(UserAccount).where(UserAccount.party_id == party_id, UserAccount.is_active.is_(True)))


def verify_identity_verification(
    db: Session, record: IdentityVerification, verifier: AdminUser, notes: str = ""
) -> IdentityVerification:
    if verifier.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    now = datetime.now(timezone.utc)
    record.status = "verified"
    record.verified_at = now
    record.expires_at = now + timedelta(days=IDENTITY_VERIFICATION_VALIDITY_DAYS)
    record.verifier_admin_id = verifier.id
    record.verifier_notes = notes
    record.updated_at = now

    user = _user_for_party(db, record.party_id)
    if user:
        notif_crud.notify_user(
            db, user.id,
            title="Identity verified",
            message="Your identity document has been approved. You can now apply to rent or publish a listing.",
            notification_type="identity_verification.approved",
            related_entity_type="identity_verification", related_entity_id=str(record.id),
        )

    db.commit()
    db.refresh(record)

    # ZR-ENG-CLR-012 Section 8/24: identity verification produces a scoped
    # credential rather than leaving "verified" as a bare status flip on the
    # raw document row -- the same "no raw document/check becomes a
    # permanent fact without a credential" doctrine already applied to
    # OCCUPANCY_ELIGIBILITY in crud/occupancy_eligibility.py.
    db.add(VerificationCredential(
        party_id=record.party_id, requirement_code="IDENTITY", status="VALID",
        method=record.document_type, jurisdiction_code="",
        source_identity_verification_id=record.id, expires_at=record.expires_at,
    ))
    db.commit()

    if user:
        send_identity_verification_approved_email(user.email, user.full_name)
    return record


def reject_identity_verification(
    db: Session, record: IdentityVerification, verifier: AdminUser, notes: str = ""
) -> IdentityVerification:
    if verifier.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    now = datetime.now(timezone.utc)
    record.status = "rejected"
    record.verifier_admin_id = verifier.id
    record.verifier_notes = notes
    record.updated_at = now

    user = _user_for_party(db, record.party_id)
    if user:
        notif_crud.notify_user(
            db, user.id,
            title="Identity verification rejected",
            message=notes or "Your identity document could not be verified. Please submit a new document.",
            notification_type="identity_verification.rejected",
            related_entity_type="identity_verification", related_entity_id=str(record.id),
        )

    db.commit()
    db.refresh(record)

    if user:
        send_identity_verification_rejected_email(user.email, user.full_name, notes)
    return record


def get_verified_identity_for_party(db: Session, party_id: int) -> IdentityVerification | None:
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(IdentityVerification)
        .where(
            IdentityVerification.party_id == party_id,
            IdentityVerification.status == "verified",
            (IdentityVerification.expires_at.is_(None)) | (IdentityVerification.expires_at > now),
        )
        .order_by(IdentityVerification.id.desc())
    )


def get_valid_identity_credential(db: Session, party_id: int) -> VerificationCredential | None:
    """ZR-ENG-CLR-012 Section 24 source-of-truth rule: downstream gates that
    care about identity as one requirement family among several (alongside
    OCCUPANCY_ELIGIBILITY, etc.) should read the credential here, not
    IdentityVerification.status directly -- kept as its own lookup rather
    than folded into get_verified_identity_for_party so callers can migrate
    independently."""
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(VerificationCredential).where(
            VerificationCredential.party_id == party_id,
            VerificationCredential.requirement_code == "IDENTITY",
            VerificationCredential.status == "VALID",
            (VerificationCredential.expires_at.is_(None)) | (VerificationCredential.expires_at > now),
        )
        .order_by(VerificationCredential.valid_from.desc())
    )


def submit_identity_verification_for_user(
    db: Session,
    user_account: "UserAccount",
    *,
    document_type: str,
    document_number: str,
    custom_document_name: str,
    stored_filename: str,
    original_filename: str,
    content_type: str,
    file_size: int,
    duplicate_of_verification_id: int | None = None,
) -> IdentityVerification:
    """User submits their own identity verification, with an uploaded document, for
    PENDING approval. The document category is always derived from document_type
    server-side (never trusted from the client) so it can't be mismatched.

    ZR-ENG-CLR-012 Section 18: "duplicate/fraud-pattern detection occur
    before reviewer exposure." duplicate_of_verification_id (the caller
    resolves it via crud/evidence_vault.py's find_duplicate_by_hash before
    this record even exists) surfaces that signal in the same review-queue
    notification an admin already sees, rather than a silent, never-flagged
    fact -- flags it as a signal to check, not a verdict, per that section's
    own framing."""
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid document type")
    if document_type == "other" and not custom_document_name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please specify the document name for 'Other'")
    if not user_account.party_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "User has no associated party")

    record = IdentityVerification(
        party_id=user_account.party_id,
        document_type=document_type,
        document_category=DOCUMENT_CATEGORY_BY_TYPE[document_type],
        custom_document_name=custom_document_name.strip() if document_type == "other" else "",
        encrypted_reference=document_number.strip() or None,
        evidence_ref="",
        document_file_path=stored_filename,
        document_file_original_name=original_filename,
        document_file_content_type=content_type,
        document_file_size=file_size,
        status="pending",
    )
    db.add(record)
    db.flush()

    # A document whose content matches an earlier submission is a fraud
    # signal a human must see -- it must never be auto-verified by the scan
    # below, which only checks what the document number looks like.
    # After MAX_AUTOMATED_SCAN_ATTEMPTS scan rejections for the same document
    # type, stop bouncing the user back to re-upload and let a reviewer
    # decide instead (the scan can be wrong about a genuine document).
    scan_failed_before = _prior_auto_flagged_count(db, record) >= MAX_AUTOMATED_SCAN_ATTEMPTS
    if duplicate_of_verification_id is None:
        ocr_outcome = _run_ocr_check(db, record, allow_reroute=not scan_failed_before)
        if ocr_outcome is not None:
            db.commit()
            db.refresh(record)
            return record

    message = f"{user_account.full_name} submitted a {document_type.replace('_', ' ')} for review."
    if duplicate_of_verification_id is not None:
        message += (
            f" Note: this document's content matches a previous submission "
            f"(verification #{duplicate_of_verification_id}) -- review for reuse or fraud."
        )
    elif scan_failed_before and record.verifier_notes:
        message += " The automated scan couldn't confirm it after repeated attempts -- please review it manually."

    notif_crud.notify_all_super_admins(
        db,
        title="Identity verification pending review",
        message=message,
        notification_type="identity_verification.submitted",
        related_entity_type="identity_verification", related_entity_id=str(record.id),
    )

    db.commit()
    db.refresh(record)

    return record


# How many scan rejections for the same document type a user gets before
# their next upload goes to a human reviewer instead of being rejected again.
MAX_AUTOMATED_SCAN_ATTEMPTS = 2


def _prior_auto_flagged_count(db: Session, record: IdentityVerification) -> int:
    return db.scalar(
        select(func.count(IdentityVerification.id)).where(
            IdentityVerification.party_id == record.party_id,
            IdentityVerification.document_type == record.document_type,
            IdentityVerification.id != record.id,
            IdentityVerification.status == "additional_evidence_required",
            IdentityVerification.verifier_admin_id.is_(None),
        )
    ) or 0


def _run_ocr_check(db: Session, record: IdentityVerification, *, allow_reroute: bool = True) -> str | None:
    """Dispatches to the real, local OCR check (services/document_ocr.py)
    appropriate for this record's category. Returns the outcome ("verified"
    or "additional_evidence_required") if OCR actually ran and decided
    something, so the caller can skip the normal "pending review"
    notification -- or None if the record should stay "pending" as normal
    (OCR unavailable/errored, or category == "other", which has no
    checkable content at all). Fails OPEN (returns None) only for infra
    problems -- never for a real bad result. With allow_reroute=False a
    failed scan also returns None (stays "pending" for a human reviewer,
    with the scan's finding recorded in verifier_notes)."""
    from app.services import document_ocr

    if record.document_category not in ("identity", "address"):
        return None
    if not document_ocr.is_available():
        return None

    if record.document_category == "identity":
        return _run_identity_ocr_check(db, record, document_ocr, allow_reroute=allow_reroute)
    return _run_address_ocr_check(db, record, document_ocr, allow_reroute=allow_reroute)


def _handle_scan_failure(db: Session, record: IdentityVerification, reason: str, *, allow_reroute: bool) -> str | None:
    if not allow_reroute:
        record.verifier_notes = (
            f"Sent to a Zoiko reviewer -- the automated scan couldn't confirm this document. Scan result: {reason}"
        )
        return None
    doc_label = record.document_type.replace("_", " ")
    user = _user_for_party(db, record.party_id)
    notif_crud.notify_all_super_admins(
        db,
        title="Identity document flagged by automated scan",
        message=(
            f"{user.full_name if user else f'Party #{record.party_id}'}'s {doc_label} was sent back for re-upload "
            f"by the automated scan: {reason} You can review it and approve or reject it manually."
        ),
        notification_type="identity_verification.auto_flagged",
        related_entity_type="identity_verification", related_entity_id=str(record.id),
    )
    return _reroute_to_additional_evidence(db, record, f"{reason} Please re-upload a clearer, well-lit photo of the full document.")


def _reroute_to_additional_evidence(db: Session, record: IdentityVerification, note: str) -> str:
    """Shared by both OCR checks below -- same real notification+email path
    a human admin's request_additional_evidence produces."""
    record.status = "additional_evidence_required"
    record.verifier_notes = note

    user = _user_for_party(db, record.party_id)
    if user:
        notif_crud.notify_user(
            db, user.id,
            title="Please re-upload your document",
            message=note,
            notification_type="identity_verification.additional_evidence_required",
            related_entity_type="identity_verification", related_entity_id=str(record.id),
        )
        send_identity_verification_additional_evidence_email(user.email, user.full_name, note)
    return "additional_evidence_required"


def _run_identity_ocr_check(db: Session, record: IdentityVerification, document_ocr, *, allow_reroute: bool = True) -> str | None:
    """A genuine match at or above that document type's own confidence
    threshold (document_ocr.confidence_threshold_for) -- plus, for Aadhaar,
    a genuinely valid Verhoeff checksum, and no conflict with whatever
    number the user typed themselves -- auto-verifies for real, using the
    same real crud path (and same real IDENTITY VerificationCredential) a
    human admin's approval click produces: this is a real check actually
    succeeding, not a blind accept, so completing it automatically is
    honest. Any of those failing reroutes to re-upload."""
    from app.core.identity_uploads import resolve_identity_document_path
    from app.crud.payment_provider import get_system_admin

    try:
        file_path = resolve_identity_document_path(record.document_file_path)
        matched_number, confidence = document_ocr.extract_and_score(file_path.read_bytes(), record.document_type)
    except Exception:
        return None

    record.ocr_extracted_number = matched_number
    record.ocr_confidence = confidence
    db.flush()

    doc_label = record.document_type.replace('_', ' ')
    threshold = document_ocr.confidence_threshold_for(record.document_type)
    typed_number = (record.encrypted_reference or "").replace(" ", "").upper()

    reject_reason: str | None = None
    if not matched_number or confidence < threshold:
        reject_reason = (
            f"Automated scan couldn't clearly read a valid {doc_label} number from this photo "
            f"(confidence {confidence:.0f}%, below the {threshold:.0f}% minimum)."
        )
    elif record.document_type == "aadhaar" and not document_ocr.is_valid_aadhaar_checksum(matched_number):
        # Format-matched (12 digits) but fails the real UIDAI checksum --
        # not a genuine Aadhaar number, regardless of how confident the OCR
        # read itself was.
        reject_reason = f"The number read from this photo ({matched_number}) is not a valid Aadhaar number."
    elif typed_number and typed_number != matched_number:
        reject_reason = (
            f"The number you entered ({record.encrypted_reference}) doesn't match the number read from the "
            f"photo ({matched_number})."
        )

    if reject_reason is None:
        note = (
            f"Auto-verified: automated scan read a {doc_label} number ({matched_number}) matching the "
            f"required format, at {confidence:.0f}% OCR confidence (minimum {threshold:.0f}%)."
        )
        verify_identity_verification(db, record, get_system_admin(db), notes=note)
        return "verified"

    return _handle_scan_failure(db, record, reject_reason, allow_reroute=allow_reroute)


def _run_address_ocr_check(db: Session, record: IdentityVerification, document_ocr, *, allow_reroute: bool = True) -> str | None:
    """No document NUMBER and nothing stored anywhere in this platform to
    check a claimed address against (see document_ocr.py's own docstring),
    so the only genuine signal is "does the content plausibly match the
    claimed document type." A plausible match auto-verifies for real, using
    the same real crud path a human admin's approval click produces --
    otherwise reroutes to re-upload with the actual reason."""
    from app.core.identity_uploads import resolve_identity_document_path
    from app.crud.payment_provider import get_system_admin

    try:
        file_path = resolve_identity_document_path(record.document_file_path)
        plausible, confidence = document_ocr.check_address_document_plausibility(
            file_path.read_bytes(), record.document_type,
        )
    except Exception:
        return None

    record.ocr_confidence = confidence
    db.flush()

    doc_label = record.document_type.replace('_', ' ')
    threshold = document_ocr.ADDRESS_OCR_CONFIDENCE_THRESHOLD

    if plausible and confidence >= threshold:
        note = (
            f"Auto-verified: automated scan found content consistent with document type '{doc_label}', "
            f"at {confidence:.0f}% OCR confidence (minimum {threshold:.0f}%)."
        )
        verify_identity_verification(db, record, get_system_admin(db), notes=note)
        return "verified"

    if not plausible:
        reason = f"Automated scan couldn't find content matching document type '{doc_label}' in this document."
    else:
        reason = (
            f"Automated scan couldn't clearly read this {doc_label} "
            f"(confidence {confidence:.0f}%, below the {threshold:.0f}% minimum)."
        )
    return _handle_scan_failure(db, record, reason, allow_reroute=allow_reroute)


def list_user_identity_verifications(db: Session, user_account: "UserAccount") -> list[IdentityVerification]:
    """List all identity verifications for a user's party."""
    if not user_account.party_id:
        return []
    return list(
        db.scalars(
            select(IdentityVerification)
            .where(IdentityVerification.party_id == user_account.party_id)
            .order_by(IdentityVerification.id.desc())
        )
    )


def request_additional_evidence(db: Session, record: IdentityVerification, verifier: AdminUser, note: str = "") -> IdentityVerification:
    """Super admin requests additional evidence from user.

    ZR-ENG-CLR-012 Section 17 MISMATCH/UNSUPPORTED_DOCUMENT handling: "Ask
    for correction/supporting evidence" rather than an outright reject.
    Mirrors verify_identity_verification/reject_identity_verification's own
    notify+email pattern so the renter actually learns what changed and why
    -- record.verifier_notes is what api/routes/user_identity.py's
    IdentityVerificationUserRead already surfaces to the renter."""
    if verifier.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    now = datetime.now(timezone.utc)
    record.status = "additional_evidence_required"
    record.verifier_admin_id = verifier.id
    record.verifier_notes = note
    record.updated_at = now

    user = _user_for_party(db, record.party_id)
    if user:
        notif_crud.notify_user(
            db, user.id,
            title="Additional evidence needed to verify your identity",
            message=note or "We need additional or different evidence before we can verify your identity.",
            notification_type="identity_verification.additional_evidence_required",
            related_entity_type="identity_verification", related_entity_id=str(record.id),
        )

    db.commit()
    db.refresh(record)

    if user:
        send_identity_verification_additional_evidence_email(user.email, user.full_name, note)
    return record

