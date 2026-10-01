import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
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

logger = logging.getLogger("uvicorn.error")

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
        send_identity_verification_approved_email(user.email, user.full_name, verification_id=record.id)
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
        send_identity_verification_rejected_email(user.email, user.full_name, notes, verification_id=record.id)
    return record


def get_verified_identity_for_party(db: Session, party_id: int) -> IdentityVerification | None:
    verify_due_submissions_for_party(db, party_id)
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
    verify_due_submissions_for_party(db, party_id)
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
        # Every upload waits AUTO_VERIFY_DELAY_SECONDS and is then verified by
        # verify_due_submissions_for_party -- except a duplicate, which a
        # human must look at, so it gets a different note and is never swept.
        verifier_notes=AUTO_VERIFY_PENDING_NOTE if duplicate_of_verification_id is None else DUPLICATE_PENDING_NOTE,
    )
    record.ocr_extracted_number, record.extracted_name = _extract_details(record, document_number)
    db.add(record)
    db.flush()

    if duplicate_of_verification_id is None:
        db.commit()
        db.refresh(record)
        return record

    logger.info(
        "identity_verification #%s: duplicate of #%s -- leaving in 'pending' for manual review",
        record.id, duplicate_of_verification_id,
    )
    message = (
        f"{user_account.full_name} submitted a {document_type.replace('_', ' ')} for review. "
        f"Note: this document's content matches a previous submission "
        f"(verification #{duplicate_of_verification_id}) -- review for reuse or fraud."
    )

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


# How long an upload shows as "Verifying..." before it is auto-verified. The
# frontend polls while a submission is pending, so users see it flip to
# verified within ~10-15 seconds.
AUTO_VERIFY_DELAY_SECONDS = 10
AUTO_VERIFY_PENDING_NOTE = "Verifying automatically."
DUPLICATE_PENDING_NOTE = "This document matches an earlier submission -- a Zoiko reviewer will check it."


def _extract_details(record: IdentityVerification, typed_number: str) -> tuple[str | None, str | None]:
    """Regex-only (no OCR) document number + name, stored on the record."""
    from app.core.identity_uploads import resolve_identity_document_path
    from app.services import document_regex

    try:
        document_bytes = resolve_identity_document_path(record.document_file_path).read_bytes()
    except OSError:
        document_bytes = b""
    return document_regex.extract_details(
        record.document_type, document_bytes, record.document_file_content_type, typed_number,
    )


def verify_due_submissions_for_party(db: Session, party_id: int) -> None:
    """Auto-verifies this party's uploads that have waited AUTO_VERIFY_DELAY_SECONDS.
    Called whenever the user's verifications are read, so there's no
    background job to lose on a server restart -- the next read catches up."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=AUTO_VERIFY_DELAY_SECONDS)
    due = [
        record for record in db.scalars(
            select(IdentityVerification).where(
                IdentityVerification.party_id == party_id,
                IdentityVerification.status == "pending",
                IdentityVerification.verifier_notes == AUTO_VERIFY_PENDING_NOTE,
            )
        )
        if _as_utc(record.created_at) <= cutoff
    ]
    if not due:
        return

    from app.crud.payment_provider import get_system_admin

    try:
        system_admin = get_system_admin(db)
    except HTTPException:
        logger.warning("identity auto-verify: no system admin configured -- leaving uploads in 'pending'")
        return
    for record in due:
        verify_identity_verification(db, record, system_admin, notes="Auto-verified on upload.")


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def list_user_identity_verifications(db: Session, user_account: "UserAccount") -> list[IdentityVerification]:
    """List all identity verifications for a user's party."""
    if not user_account.party_id:
        return []
    verify_due_submissions_for_party(db, user_account.party_id)
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
        send_identity_verification_additional_evidence_email(user.email, user.full_name, note, verification_id=record.id)
    return record

