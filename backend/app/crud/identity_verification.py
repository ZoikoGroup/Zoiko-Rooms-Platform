"""Identity verification records -- the admin queue, the reviewer actions
and the gates other modules call. The verification logic itself lives in
services/identity/service.py (ZR-IDENTITY-001); this module keeps the
long-standing function names working on top of it."""

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.crud.party import get_or_create_default_party
from app.models.admin_user import AdminUser
from app.models.identity_verification import DOCUMENT_CATEGORY_BY_TYPE, IdentityVerification, DOCUMENT_TYPES
from app.models.party import Party
from app.models.user_account import UserAccount
from app.models.verification_credential import VerificationCredential
from app.schemas.marketplace import IdentityVerificationCreate
from app.services.identity import policy
from app.services.identity import service as identity_service

logger = logging.getLogger("uvicorn.error")

# Pseudo-status for the admin review queue: everything a reviewer should act on.
NEEDS_REVIEW_FILTER = "needs_review"


def list_identity_verifications(
    db: Session,
    admin: AdminUser,
    party_id: int | None = None,
    status: str | None = None,
) -> list[IdentityVerification]:
    query = select(IdentityVerification).where(IdentityVerification.session_state != "IN_PROGRESS")
    if admin.role != "super_admin":
        party = get_or_create_default_party(db, admin)
        query = query.where(IdentityVerification.party_id == party.id)
    elif party_id is not None:
        query = query.where(IdentityVerification.party_id == party_id)
    if status == NEEDS_REVIEW_FILTER:
        # Escalated cases first, then oldest submission first.
        query = query.where(IdentityVerification.session_state == "PENDING_REVIEW").order_by(
            IdentityVerification.escalated_at.is_(None), IdentityVerification.submitted_at, IdentityVerification.id,
        )
        return list(db.scalars(query))
    if status is not None:
        query = query.where(or_(IdentityVerification.status == status, IdentityVerification.session_state == status))
    return list(db.scalars(query.order_by(IdentityVerification.id.desc())))


def get_identity_verification(db: Session, verification_id: int) -> IdentityVerification | None:
    return db.get(IdentityVerification, verification_id)


def submit_identity_verification(db: Session, admin: AdminUser, data: IdentityVerificationCreate) -> IdentityVerification:
    """An admin records a verification on a party's behalf; it waits in the
    review queue like any other submission."""
    from app.core.field_encryption import encrypt_text
    from app.services.identity.providers import mask_number, number_hash

    if data.document_type not in DOCUMENT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid document type")
    if not data.encrypted_reference:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Document number is required")

    if data.party_id is not None:
        if admin.role != "super_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins may create verification records for another party")
        party = db.get(Party, data.party_id)
        if not party:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")
    else:
        party = get_or_create_default_party(db, admin)

    number = data.encrypted_reference.strip()
    record = IdentityVerification(
        party_id=party.id,
        document_type=data.document_type,
        document_category=DOCUMENT_CATEGORY_BY_TYPE.get(data.document_type, "identity"),
        encrypted_reference=encrypt_text(number),
        masked_document_number=mask_number(number),
        document_number_hash=number_hash(data.document_type, number),
        evidence_ref=data.evidence_ref,
        status="pending",
        session_state="PENDING_REVIEW",
        method_type="MANUAL",
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def _ensure_reviewable(record: IdentityVerification) -> None:
    """A row written directly with only the legacy `status` (older data,
    fixtures) gets the matching session state before a reviewer acts."""
    if record.session_state == "IN_PROGRESS" and record.status == "pending":
        record.session_state = "PENDING_REVIEW"


def verify_identity_verification(
    db: Session, record: IdentityVerification, verifier: AdminUser, notes: str = ""
) -> IdentityVerification:
    _ensure_reviewable(record)
    return identity_service.reviewer_decision(
        db, verifier, record, decision="APPROVE", reason_code="REVIEWER_APPROVED",
        note=notes.strip() or "Approved by a reviewer",
    )


def reject_identity_verification(
    db: Session, record: IdentityVerification, verifier: AdminUser, notes: str = ""
) -> IdentityVerification:
    _ensure_reviewable(record)
    return identity_service.reviewer_decision(
        db, verifier, record, decision="REJECT", reason_code="REVIEWER_REJECTED",
        note=notes.strip() or "Rejected by a reviewer",
    )


def request_additional_evidence(db: Session, record: IdentityVerification, verifier: AdminUser, note: str = "") -> IdentityVerification:
    _ensure_reviewable(record)
    return identity_service.reviewer_decision(
        db, verifier, record, decision="ACTION_REQUIRED", reason_code="MORE_INFORMATION_NEEDED",
        note=note.strip() or "We need additional or different evidence before we can verify your identity.",
    )


def get_verified_identity_for_party(db: Session, party_id: int) -> IdentityVerification | None:
    """The gate every publication / application / agreement check uses
    (ZR-IDENTITY-001 Section 8.4): reads the account-level identity profile.
    A party with no profile yet (data from before profiles existed) falls
    back to its latest verified record."""
    if identity_service.get_profile(db, party_id) is not None:
        return identity_service.is_verified(db, party_id)
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
    treat identity as one requirement family among several read the
    credential here. Re-verification revokes it
    (services/identity/service.py:mark_reverification_required)."""
    profile = identity_service.get_profile(db, party_id)
    if profile is not None:
        identity_service.refresh_profile(db, profile)
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
    """One-step upload (the older single-form screen): starts or reuses a
    session, attaches the document and -- when the person's legal details
    are complete -- submits it to the same checks as the step-by-step flow.
    With details still missing the document is saved and the session waits
    in IN_PROGRESS for them (nothing is approved without them)."""
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid document type")
    if document_type == "other" and not custom_document_name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please specify the document name for 'Other'")
    if not user_account.party_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "User has no associated party")

    profile = identity_service.get_or_create_profile(db, user_account.party_id)
    if not profile.country_code:
        party = db.get(Party, user_account.party_id)
        profile.country_code = policy.country_from_jurisdiction(party.jurisdiction if party else "")
        db.flush()
    session = identity_service.start_session(db, user_account, method="DOCUMENT")
    session = identity_service.attach_document(
        db, user_account, session, document_type=document_type, document_number=document_number,
        stored_filename=stored_filename, original_filename=original_filename, content_type=content_type,
        file_size=file_size, duplicate_of_verification_id=duplicate_of_verification_id,
    )
    pack = policy.get_pack(db, profile.country_code)
    if not identity_service._details_complete(profile, pack):
        return session
    return identity_service.submit(db, user_account, session, attested=True)


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
