"""Identity verification records -- the admin list and the gates other
modules call. The verification logic itself lives in
services/identity/service.py (ZR-IDENTITY-001); identities are decided only
by the identity provider (Veriff, ZR-IDV-ADR-001), never by a person here."""

import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.crud.party import get_or_create_default_party
from app.models.admin_user import AdminUser
from app.models.identity_verification import IdentityVerification
from app.models.user_account import UserAccount
from app.models.verification_credential import VerificationCredential
from app.services.identity import service as identity_service

logger = logging.getLogger("uvicorn.error")

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
    if status is not None:
        query = query.where(or_(IdentityVerification.status == status, IdentityVerification.session_state == status))
    return list(db.scalars(query.order_by(IdentityVerification.id.desc())))


def get_identity_verification(db: Session, verification_id: int) -> IdentityVerification | None:
    return db.get(IdentityVerification, verification_id)


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
