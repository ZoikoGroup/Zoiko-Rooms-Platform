from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.mailer import send_identity_verification_approved_email, send_identity_verification_rejected_email
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
    if status is not None:
        query = query.where(IdentityVerification.status == status)
    return list(db.scalars(query))


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


def verify_identity_verification(db: Session, record: IdentityVerification, verifier: AdminUser) -> IdentityVerification:
    if verifier.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    now = datetime.now(timezone.utc)
    record.status = "verified"
    record.verified_at = now
    record.expires_at = now + timedelta(days=IDENTITY_VERIFICATION_VALIDITY_DAYS)
    record.verifier_admin_id = verifier.id
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
) -> IdentityVerification:
    """User submits their own identity verification, with an uploaded document, for
    PENDING approval. The document category is always derived from document_type
    server-side (never trusted from the client) so it can't be mismatched."""
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

    notif_crud.notify_all_super_admins(
        db,
        title="Identity verification pending review",
        message=f"{user_account.full_name} submitted a {document_type.replace('_', ' ')} for review.",
        notification_type="identity_verification.submitted",
        related_entity_type="identity_verification", related_entity_id=str(record.id),
    )

    db.commit()
    db.refresh(record)
    return record


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
    """Super admin requests additional evidence from user."""
    if verifier.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    now = datetime.now(timezone.utc)
    record.status = "additional_evidence_required"
    record.verifier_admin_id = verifier.id
    record.updated_at = now
    db.commit()
    db.refresh(record)
    return record

