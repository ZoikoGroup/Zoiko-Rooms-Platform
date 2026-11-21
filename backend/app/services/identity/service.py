"""ZR-IDENTITY-001 orchestration: the account-level identity profile, its
verification sessions, and re-verification.

The identity check is a FACE MATCH run on this server (services/identity/face_match.py): the person uploads a photo
of their identity document, takes one selfie, and if the face on the document matches the selfie they are verified.
Nothing is read from the document and there is no liveness test, so this proves "the selfie looks like the picture
they uploaded", not that the document is genuine, in date or in their name.

Server-authoritative: nothing a client sends can mark a person verified (Section 8.4) -- only the server's own
comparison in verify_faces. There is no manual-review route.

Flow: start_session -> submit (they confirm their details; photo step opens) -> verify_faces (document photo + selfie
in one request; compared in memory, never stored) -> VERIFIED / ACTION_REQUIRED (clearer photos) / FAILED.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import notification as notif_crud
from app.crud.events import emit_event
from app.models.admin_user import AdminUser
from app.models.identity_profile import ASSURANCE_LEVELS, IdentityProfile, ROLE_CONTEXTS, VERIFICATION_METHODS
from app.models.identity_verification import IdentityVerification
from app.models.user_account import UserAccount
from app.models.verification_credential import VerificationCredential
from app.services.identity import policy, providers
from app.services.identity.reason_codes import describe

logger = logging.getLogger("uvicorn.error")

# Section 7.1 transitions a verification session may make.
_SESSION_TRANSITIONS: dict[str, set[str]] = {
    "IN_PROGRESS": {"PROCESSING", "IN_PROGRESS"},
    "PROCESSING": {"VERIFIED", "ACTION_REQUIRED", "FAILED", "IN_PROGRESS"},
    # Only rows from before the manual route was removed can be here; they
    # can only be closed.
    "PENDING_REVIEW": {"FAILED"},
    "ACTION_REQUIRED": {"IN_PROGRESS", "FAILED"},
    "VERIFIED": {"REVERIFICATION_REQUIRED"},
    "FAILED": set(),
    "REVERIFICATION_REQUIRED": set(),
}
# Open sessions can be resumed instead of starting another.
OPEN_SESSION_STATES = ("IN_PROGRESS", "ACTION_REQUIRED")
# Legacy `status` kept in step for older readers (models/identity_verification.py).
_LEGACY_STATUS = {
    "IN_PROGRESS": "draft", "PROCESSING": "pending", "PENDING_REVIEW": "pending",
    "ACTION_REQUIRED": "additional_evidence_required", "VERIFIED": "verified",
    "FAILED": "rejected", "REVERIFICATION_REQUIRED": "expired",
}
# Section 3.2 dashboard presentation per account state.
_DASHBOARD = {
    "NOT_STARTED": ("Identity not verified", "Verify identity"),
    "IN_PROGRESS": ("Verification in progress", "Continue"),
    "PROCESSING": ("Verification in review", "View status"),
    "PENDING_REVIEW": ("Verification in review", "View status"),
    "ACTION_REQUIRED": ("Identity action required", "Fix verification"),
    "VERIFIED": ("Identity Verified", None),
    "REVERIFICATION_REQUIRED": ("Identity verification required", "Verify again"),
    "FAILED": ("Could not verify", "Try another method"),
}
_HANDOFF_TTL = timedelta(minutes=15)
# Section 2.3: a face match proves the selfie looks like the picture on the document and nothing more (no document
# authenticity check, no liveness), so it is the lowest assurance level.
_PROVIDER_PASS_LEVEL = "IV-0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# -- events (Section 12.3) ---------------------------------------------------

def _event(db: Session, event_type: str, profile: IdentityProfile, session: IdentityVerification | None, *,
           reason_codes: list[str] | None = None, actor_kind: str = "system", actor_id: str = "",
           previous_state: str | None = None, new_state: str | None = None, correlation_id: str = "") -> None:
    """Safe payload only: ids, states, method, reason codes -- never names,
    numbers, dates of birth or images (Section 9.2)."""
    emit_event(
        db, event_type, "identity_verification", str(session.id) if session else f"profile:{profile.id}",
        {
            "partyId": profile.party_id, "identityProfileId": profile.id,
            "verificationId": session.id if session else None,
            "method": session.method_type if session else profile.verification_method,
            "reasonCodes": list(reason_codes or []),
        },
        correlation_id=correlation_id, actor_kind=actor_kind, actor_id=actor_id,
        previous_state=previous_state, new_state=new_state,
    )


# -- profile -----------------------------------------------------------------

def _split_name(full_name: str) -> tuple[str, str, str]:
    parts = (full_name or "").split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def _user_for_party(db: Session, party_id: int) -> UserAccount | None:
    return db.scalar(select(UserAccount).where(UserAccount.party_id == party_id, UserAccount.is_active.is_(True)))


def get_profile(db: Session, party_id: int) -> IdentityProfile | None:
    return db.scalar(select(IdentityProfile).where(IdentityProfile.party_id == party_id))


def get_or_create_profile(db: Session, party_id: int) -> IdentityProfile:
    profile = get_profile(db, party_id)
    if profile is not None:
        refresh_profile(db, profile)
        return profile
    user = _user_for_party(db, party_id)
    given, middle, family = _split_name(user.full_name if user else "")
    profile = IdentityProfile(
        party_id=party_id, state="NOT_STARTED", assurance_level="IV-0",
        given_name=given, middle_names=middle, family_name=family,
    )
    db.add(profile)
    db.flush()
    return profile


def refresh_profile(db: Session, profile: IdentityProfile) -> None:
    """Applies the policy re-verification date (never document expiry)."""
    due = _as_utc(profile.reverification_required_at)
    if profile.state == "VERIFIED" and due is not None and due <= _now():
        mark_reverification_required(db, profile, "PERIODIC_RENEWAL")


def is_verified(db: Session, party_id: int) -> IdentityVerification | None:
    """The session that established a currently valid identity, or None.
    What every publication / booking gate checks (Section 8.4)."""
    profile = get_profile(db, party_id)
    if profile is None:
        return None
    refresh_profile(db, profile)
    if profile.state != "VERIFIED" or not profile.current_verification_id:
        return None
    return db.get(IdentityVerification, profile.current_verification_id)


def dashboard(profile: IdentityProfile) -> dict:
    header, action = _DASHBOARD.get(profile.state, _DASHBOARD["NOT_STARTED"])
    info = describe(profile.reason_codes or [])
    return {
        "state": profile.state,
        "header": header,
        "primary_action": action,
        # Section 3.2: one clear reason only when action is needed.
        "attention": profile.state in ("ACTION_REQUIRED", "REVERIFICATION_REQUIRED", "FAILED"),
        "message": info["message"],
        "actions": info["actions"],
    }


def update_details(db: Session, user: UserAccount, *, given_name: str, middle_names: str, family_name: str,
                   date_of_birth: date | None, country_code: str, current_password: str = "",
                   correlation_id: str = "") -> IdentityProfile:
    """Section 5.2. A material change to a verified legal name or date of
    birth needs the account password again (Section 9.3 step-up) and
    triggers re-verification (Section 7.3)."""
    party_id = _require_party(user)
    profile = get_or_create_profile(db, party_id)
    given, middle, family = given_name.strip(), middle_names.strip(), family_name.strip()
    if not given or not family:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter your given name and family name")
    country = policy.normalize_country(country_code)
    if not country:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose your country or territory")
    pack = policy.get_pack(db, country)
    if pack.date_of_birth_required and date_of_birth is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter your date of birth")
    if date_of_birth is not None and date_of_birth >= _now().date():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter a valid date of birth")

    new_name = " ".join(p for p in (given, middle, family) if p)
    new_dob = date_of_birth if pack.date_of_birth_required or date_of_birth else None
    material = (
        _normal(profile.legal_name) != _normal(new_name)
        or (profile.date_of_birth is not None and profile.date_of_birth != new_dob)
    )
    if profile.state == "VERIFIED" and material:
        from app.core.security import verify_password

        if not current_password or not verify_password(current_password, user.hashed_password):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Enter your account password to change details on a verified identity",
            )
    profile.given_name, profile.middle_names, profile.family_name = given, middle, family
    profile.date_of_birth = new_dob
    profile.country_code = country
    profile.updated_at = _now()

    if profile.state == "VERIFIED" and material:
        _event(db, "IDENTITY_PROFILE_MATERIAL_CHANGE", profile, None, actor_kind="user", actor_id=str(user.id),
               correlation_id=correlation_id)
        mark_reverification_required(db, profile, "LEGAL_DETAILS_CHANGED", correlation_id=correlation_id)
        notif_crud.notify_user(
            db, user.id, title="Please verify your identity again",
            message="You changed your legal name or date of birth, so we need to confirm your identity again.",
            notification_type="identity_verification.reverification_required",
        )
    db.commit()
    db.refresh(profile)
    return profile


def _normal(name: str) -> str:
    return " ".join(name.casefold().split())


def _require_party(user: UserAccount) -> int:
    if not user.party_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "User has no associated party")
    return user.party_id


def _details_complete(profile: IdentityProfile, pack) -> bool:
    if not profile.given_name.strip() or not profile.family_name.strip() or not profile.country_code:
        return False
    return not (pack.date_of_birth_required and profile.date_of_birth is None)


# -- re-verification (Section 7.2 / 7.3) -------------------------------------

def mark_reverification_required(db: Session, profile: IdentityProfile, reason: str, *, correlation_id: str = "") -> None:
    if profile.state != "VERIFIED":
        return
    previous = profile.state
    profile.state = "REVERIFICATION_REQUIRED"
    profile.reverification_reason = reason
    profile.reason_codes = [reason]
    profile.updated_at = _now()
    for credential in db.scalars(select(VerificationCredential).where(
        VerificationCredential.party_id == profile.party_id,
        VerificationCredential.requirement_code == "IDENTITY",
        VerificationCredential.status == "VALID",
    )):
        credential.status = "REVOKED"
        credential.revoked_at = _now()
        credential.revoked_reason = f"Re-verification required: {reason}"
    current = db.get(IdentityVerification, profile.current_verification_id) if profile.current_verification_id else None
    if current is not None:
        current.session_state = "REVERIFICATION_REQUIRED"
        current.status = _LEGACY_STATUS["REVERIFICATION_REQUIRED"]
    _event(db, "IDENTITY_REVERIFICATION_REQUIRED", profile, current, reason_codes=[reason],
           previous_state=previous, new_state=profile.state, correlation_id=correlation_id)
    db.flush()
    # ZR-AUTHORITY-002 Section 13.1: listing authority rests on this identity.
    from app.services.authority_service import reopen_for_identity_event

    reopen_for_identity_event(db, profile.party_id, reason, correlation_id=correlation_id)
    db.flush()


def on_account_recovery(db: Session, user: UserAccount) -> None:
    """Called after a password reset / account recovery."""
    if not user.party_id:
        return
    profile = get_profile(db, user.party_id)
    if profile is None or profile.state != "VERIFIED":
        return
    if policy.get_pack(db, profile.country_code).reverify_on_account_recovery:
        mark_reverification_required(db, profile, "ACCOUNT_RECOVERY")
        db.commit()


def sweep_reverification_due(db: Session) -> int:
    """Scheduled job: verified profiles whose policy renewal date passed."""
    due = list(db.scalars(select(IdentityProfile).where(
        IdentityProfile.state == "VERIFIED",
        IdentityProfile.reverification_required_at.is_not(None),
        IdentityProfile.reverification_required_at <= _now(),
    )))
    for profile in due:
        mark_reverification_required(db, profile, "PERIODIC_RENEWAL")
    db.commit()
    return len(due)


# -- sessions ------------------------------------------------------------------

def _transition(session: IdentityVerification, target: str) -> None:
    if target not in _SESSION_TRANSITIONS.get(session.session_state, set()):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This verification can't move from {session.session_state} to {target}",
        )
    session.session_state = target
    session.status = _LEGACY_STATUS[target]
    session.updated_at = _now()


def _set_profile_state(profile: IdentityProfile, state: str, reason_codes: list[str] | None = None) -> None:
    profile.state = state
    profile.reason_codes = list(reason_codes or [])
    profile.updated_at = _now()


def get_session_for_user(db: Session, user: UserAccount, session_id: int) -> IdentityVerification:
    session = db.get(IdentityVerification, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    if session.party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own identity verifications")
    return session


def start_session(db: Session, user: UserAccount, *, method: str = "DOCUMENT", role_context: str = "",
                  idempotency_key: str | None = None, correlation_id: str = "") -> IdentityVerification:
    """Create or reuse a session (Section 8.3: idempotent; never asks a
    verified person to prove their identity again)."""
    party_id = _require_party(user)
    profile = get_or_create_profile(db, party_id)
    if profile.state == "VERIFIED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Your identity is already verified")
    method = method.upper()
    if method not in VERIFICATION_METHODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"method must be one of {VERIFICATION_METHODS}")
    pack = policy.get_pack(db, profile.country_code)
    if method not in (pack.available_methods or []):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This verification method isn't available in your country")
    role = role_context.upper()
    if role and role not in ROLE_CONTEXTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"role must be one of {ROLE_CONTEXTS}")

    if idempotency_key:
        existing = db.scalar(select(IdentityVerification).where(
            IdentityVerification.party_id == party_id, IdentityVerification.idempotency_key == idempotency_key,
        ))
        if existing is not None:
            return existing
    open_session = db.scalar(
        select(IdentityVerification)
        .where(IdentityVerification.party_id == party_id, IdentityVerification.session_state.in_(OPEN_SESSION_STATES))
        .order_by(IdentityVerification.id.desc())
    )
    if open_session is not None:
        if open_session.method_type != method:
            open_session.method_type = method
            _event(db, "IDENTITY_METHOD_SELECTED", profile, open_session, actor_kind="user", actor_id=str(user.id),
                   correlation_id=correlation_id)
        if role:
            open_session.role_context = role
        db.commit()
        return open_session

    previous = profile.state
    session = IdentityVerification(
        party_id=party_id, document_type="", document_category="identity", session_state="IN_PROGRESS",
        status=_LEGACY_STATUS["IN_PROGRESS"], method_type=method, role_context=role,
        country_code=profile.country_code, idempotency_key=idempotency_key, verifier_notes="",
    )
    db.add(session)
    db.flush()
    _set_profile_state(profile, "IN_PROGRESS")
    _event(db, "IDENTITY_VERIFICATION_STARTED", profile, session, actor_kind="user", actor_id=str(user.id),
           previous_state=previous, new_state="IN_PROGRESS", correlation_id=correlation_id)
    _event(db, "IDENTITY_METHOD_SELECTED", profile, session, actor_kind="user", actor_id=str(user.id),
           correlation_id=correlation_id)
    db.commit()
    db.refresh(session)
    return session


def submit(db: Session, user: UserAccount, session: IdentityVerification, *, attested: bool,
           correlation_id: str = "") -> IdentityVerification:
    """Section 5.6 review & attest, then open the photo step. The result comes only from the server-side face
    match (verify_faces). If the face models are not installed nothing is verified: the session stays open
    with PROVIDER_UNAVAILABLE and the person is told to try later."""
    if not attested:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Confirm the information is accurate and that it's your own identity",
        )
    profile = get_or_create_profile(db, session.party_id)
    pack = policy.get_pack(db, profile.country_code)
    if session.session_state != "IN_PROGRESS":
        raise HTTPException(status.HTTP_409_CONFLICT, "This verification has already been submitted")
    if not _details_complete(profile, pack):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, describe(["DETAILS_INCOMPLETE"])["message"])
    from app.services.identity import face_match

    if not face_match.is_available():
        session.attested_at = _now()
        return _provider_unavailable(db, session, profile, correlation_id)

    if pack.minimum_age and profile.date_of_birth and policy.age_on(profile.date_of_birth) < pack.minimum_age:
        session.attested_at = _now()
        session.submitted_at = _now()
        session.legal_name_snapshot = profile.legal_name
        session.country_code = profile.country_code
        _transition(session, "PROCESSING")
        db.flush()
        result = providers.NormalizedResult(
            provider_code="zoiko_policy", normalized_outcome="FAIL", reason_codes=["AGE_REQUIREMENT_NOT_MET"],
        )
        return apply_result(db, session, result, correlation_id=correlation_id)
    return _open_capture(db, session, profile, pack, correlation_id=correlation_id)


def _provider_unavailable(db: Session, session: IdentityVerification, profile: IdentityProfile,
                          correlation_id: str) -> IdentityVerification:
    """Progress is kept: back to IN_PROGRESS so the person can retry."""
    _transition(session, "IN_PROGRESS")
    session.reason_codes = ["PROVIDER_UNAVAILABLE"]
    _set_profile_state(profile, "IN_PROGRESS", ["PROVIDER_UNAVAILABLE"])
    _event(db, "IDENTITY_PROVIDER_RESULT_RECEIVED", profile, session, reason_codes=["PROVIDER_UNAVAILABLE"],
           correlation_id=correlation_id)
    db.commit()
    db.refresh(session)
    return session


# States a provider decision may still change. Anything else is final for
# this attempt (ZR-IDV-ADR-001 Section 13: never regress VERIFIED).
_DECIDABLE_BY_PROVIDER = ("IN_PROGRESS", "PROCESSING", "ACTION_REQUIRED")


def apply_result(db: Session, session: IdentityVerification, result: providers.NormalizedResult, *,
                 correlation_id: str = "") -> IdentityVerification:
    """Maps a normalized provider outcome onto the session and profile.
    Only a normalized PASS can verify."""
    profile = get_or_create_profile(db, session.party_id)
    if session.session_state not in _DECIDABLE_BY_PROVIDER:
        same_attempt = (result.provider_attempt_id or "") == (session.provider_attempt_id or "")
        if session.session_state == "VERIFIED" and result.normalized_outcome == "FAIL" and same_attempt:
            # The provider later invalidated evidence it had passed (Section 7.3).
            # Only for the attempt that verified -- a late decline from an
            # earlier attempt says nothing about the approved one.
            mark_reverification_required(db, profile, "EVIDENCE_INVALIDATED", correlation_id=correlation_id)
            db.commit()
        return session
    if session.session_state == "ACTION_REQUIRED":
        _transition(session, "IN_PROGRESS")  # a resubmission came back
    if session.session_state == "IN_PROGRESS":
        _transition(session, "PROCESSING")
    if result.provider_decision:
        session.provider_decision = result.provider_decision
    if result.provider_attempt_id:
        session.provider_attempt_id = result.provider_attempt_id
    session.normalized_outcome = result.normalized_outcome
    session.reason_codes = list(result.reason_codes)
    session.provider_code = session.provider_code or result.provider_code
    if result.provider_session_id:
        session.provider_session_id = result.provider_session_id
    session.match_results = {**(session.match_results or {}), **result.match_results,
                             "risk_signals": list(result.risk_signals)}
    if result.provider_decided_at:
        session.match_results["provider_decided_at"] = result.provider_decided_at
    if result.document_metadata.get("masked_document_number") and not session.masked_document_number:
        session.masked_document_number = result.document_metadata["masked_document_number"]
    session.ocr_extracted_number = result.document_metadata.get("masked_document_number") or session.ocr_extracted_number
    _event(db, "IDENTITY_PROVIDER_RESULT_RECEIVED", profile, session, reason_codes=result.reason_codes,
           correlation_id=correlation_id)

    outcome = result.normalized_outcome
    if outcome == "PASS":
        _verify(db, session, profile, assurance=_PROVIDER_PASS_LEVEL, correlation_id=correlation_id,
                verified_attributes=result.verified_attributes)
    elif outcome == "REVIEW":
        # The provider's own review team is looking: still "checking" -- the
        # provider decides, never a Zoiko reviewer.
        if session.session_state != "PROCESSING":
            _transition(session, "PROCESSING")
        _set_profile_state(profile, "PROCESSING", result.reason_codes)
    elif outcome == "ACTION_REQUIRED":
        _to_action_required(db, session, profile, result.reason_codes, correlation_id=correlation_id)
    else:
        _to_failed(db, session, profile, result.reason_codes, correlation_id=correlation_id)
    db.commit()
    db.refresh(session)
    return session


def _verify(db: Session, session: IdentityVerification, profile: IdentityProfile, *, assurance: str,
            correlation_id: str = "", verified_attributes: dict | None = None) -> None:
    assert assurance in ASSURANCE_LEVELS
    was_verified_before = profile.verified_at is not None
    previous = profile.state
    now = _now()
    pack = policy.get_pack(db, profile.country_code)
    renew_at = now + timedelta(days=pack.reverification_interval_days) if pack.reverification_interval_days else None

    _transition(session, "VERIFIED")
    session.assurance_level = assurance
    session.decided_at = now
    session.verified_at = now
    # Section 7.2: identity validity is policy-driven, not document expiry.
    session.expires_at = renew_at
    session.verifier_admin_id = None
    session.verifier_notes = ""
    profile.state = "VERIFIED"
    profile.assurance_level = assurance
    profile.reason_codes = []
    attributes = verified_attributes or {}
    profile.verified_legal_name = attributes.get("legal_name") or session.legal_name_snapshot or profile.legal_name
    if pack.date_of_birth_required and attributes.get("date_of_birth") and profile.date_of_birth is None:
        try:
            profile.date_of_birth = date.fromisoformat(attributes["date_of_birth"])
        except ValueError:
            pass
    profile.verified_at = now
    profile.reverification_required_at = renew_at
    profile.reverification_reason = ""
    profile.verification_method = session.method_type
    profile.provider_code = session.provider_code
    profile.provider_subject_reference = profile.provider_subject_reference or session.provider_session_id
    profile.current_verification_id = session.id
    profile.updated_at = now
    db.add(VerificationCredential(
        party_id=session.party_id, requirement_code="IDENTITY", status="VALID", method=session.method_type,
        jurisdiction_code=profile.country_code, policy_pack_version=pack.version,
        source_identity_verification_id=session.id, expires_at=renew_at,
    ))
    _event(db, "IDENTITY_REVERIFIED" if was_verified_before else "IDENTITY_VERIFIED", profile, session,
           actor_kind="system", previous_state=previous, new_state="VERIFIED", correlation_id=correlation_id)

    user = _user_for_party(db, session.party_id)
    if user:
        notif_crud.notify_user(
            db, user.id, title="Identity verified",
            message="Your identity check is complete. You can now apply to rent or publish a listing.",
            notification_type="identity_verification.approved",
            related_entity_type="identity_verification", related_entity_id=str(session.id),
        )
        from app.core.mailer import send_identity_verification_approved_email

        db.flush()
        send_identity_verification_approved_email(user.email, user.full_name, verification_id=session.id)


def _to_action_required(db: Session, session: IdentityVerification, profile: IdentityProfile, reason_codes: list[str],
                        *, correlation_id: str = "") -> None:
    previous = profile.state
    _transition(session, "ACTION_REQUIRED")
    session.decided_at = _now()
    message = describe(reason_codes)["message"]
    session.verifier_notes = message
    _set_profile_state(profile, "ACTION_REQUIRED", reason_codes)
    _event(db, "IDENTITY_ACTION_REQUIRED", profile, session, reason_codes=reason_codes,
           actor_kind="system",
           previous_state=previous, new_state="ACTION_REQUIRED", correlation_id=correlation_id)
    user = _user_for_party(db, session.party_id)
    if user:
        notif_crud.notify_user(
            db, user.id, title="We need another step to verify your identity", message=message,
            notification_type="identity_verification.additional_evidence_required",
            related_entity_type="identity_verification", related_entity_id=str(session.id),
        )
        from app.core.mailer import send_identity_verification_additional_evidence_email

        db.flush()
        send_identity_verification_additional_evidence_email(user.email, user.full_name, message, verification_id=session.id)


def _to_failed(db: Session, session: IdentityVerification, profile: IdentityProfile, reason_codes: list[str], *,
               correlation_id: str = "") -> None:
    previous = profile.state
    _transition(session, "FAILED")
    session.decided_at = _now()
    message = describe(reason_codes)["message"]
    session.verifier_notes = message
    _set_profile_state(profile, "FAILED", reason_codes)
    _event(db, "IDENTITY_VERIFICATION_FAILED", profile, session, reason_codes=reason_codes,
           actor_kind="system",
           previous_state=previous, new_state="FAILED", correlation_id=correlation_id)
    user = _user_for_party(db, session.party_id)
    if user:
        notif_crud.notify_user(
            db, user.id, title="We couldn't verify your identity", message=message,
            notification_type="identity_verification.rejected",
            related_entity_type="identity_verification", related_entity_id=str(session.id),
        )
        from app.core.mailer import send_identity_verification_rejected_email

        db.flush()
        send_identity_verification_rejected_email(user.email, user.full_name, message, verification_id=session.id)


# -- resume / phone handoff ------------------------------------------------------

def issue_handoff_token(db: Session, session: IdentityVerification) -> str:
    """Section 5.3 "Continue on phone" / 8.3 resume: a fresh short-lived,
    single-use token; any earlier one stops working."""
    if session.session_state not in OPEN_SESSION_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an unfinished verification can be continued")
    token = secrets.token_urlsafe(24)
    session.handoff_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session.handoff_expires_at = _now() + _HANDOFF_TTL
    db.commit()
    return token


def claim_handoff_token(db: Session, user: UserAccount, token: str) -> IdentityVerification:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = db.scalar(select(IdentityVerification).where(IdentityVerification.handoff_token_hash == digest))
    if session is None or session.party_id != user.party_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link isn't valid -- start again from your computer")
    expires = _as_utc(session.handoff_expires_at)
    session.handoff_token_hash = ""  # single use
    session.handoff_expires_at = None
    db.commit()
    if expires is None or expires < _now():
        raise HTTPException(status.HTTP_410_GONE, "This link has expired -- start again from your computer")
    return session


# -- hosted provider sessions (ZR-IDV-ADR-001 Section 6) ------------------------


_RESTARTABLE = ("SESSION_EXPIRED", "SESSION_ABANDONED", "PROVIDER_UNAVAILABLE", "PROVIDER_DECLINED")  # open sessions only


def restart(db: Session, user: UserAccount, session: IdentityVerification, *, correlation_id: str = "") -> IdentityVerification:
    """A new attempt after a declined one, within the pack's daily attempt limit."""
    profile = get_or_create_profile(db, session.party_id)
    pack = policy.get_pack(db, profile.country_code)
    if profile.state == "VERIFIED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Your identity is already verified")
    restartable = session.session_state == "FAILED" or (
        session.session_state in OPEN_SESSION_STATES and set(session.reason_codes or []) & set(_RESTARTABLE)
    )
    if not restartable:
        raise HTTPException(status.HTTP_409_CONFLICT, "This verification can't be restarted -- continue it instead")
    if "AGE_REQUIREMENT_NOT_MET" in (session.reason_codes or []):
        # Under the minimum age can only be retried once the date of birth on the account has been corrected to one
        # that meets it (a mistyped year would otherwise lock the person out for good).
        corrected = bool(profile.date_of_birth and pack.minimum_age
                         and policy.age_on(profile.date_of_birth) >= pack.minimum_age)
        if not corrected:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This verification can't be restarted. If your date of birth is wrong, correct it in your details first",
            )
    recent = db.scalar(select(func.count(IdentityVerification.id)).where(
        IdentityVerification.party_id == session.party_id,
        IdentityVerification.created_at >= _now() - timedelta(days=1),
    )) or 0
    if recent >= (pack.max_attempts_per_day or 1):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "You've reached today's verification attempts -- try again tomorrow")
    if session.session_state in OPEN_SESSION_STATES:
        if session.session_state == "IN_PROGRESS":
            _transition(session, "PROCESSING")
            _transition(session, "FAILED")
        else:
            _transition(session, "FAILED")
        session.reason_codes = list(dict.fromkeys([*(session.reason_codes or []), "RESTARTED"]))
        db.flush()
    _set_profile_state(profile, "NOT_STARTED")
    db.flush()
    return start_session(db, user, method="DOCUMENT",
                         role_context=session.role_context, correlation_id=correlation_id)


# -- privacy: erasure across Zoiko and the provider (ZR-IDV-ADR-001 Section 12) --

def erase_identity_data(db: Session, admin: AdminUser, party_id: int, *, reason: str, correlation_id: str = "") -> dict:
    """A lawful deletion request: removes stored evidence, document numbers,
    names and dates of birth, and leaves the person unverified. Decisions and
    the audit trail (which hold no identity content) are kept. (Face-match photos are never stored.)"""
    from app.core.identity_uploads import delete_identity_document

    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the reason for the erasure request")
    sessions = list(db.scalars(select(IdentityVerification).where(IdentityVerification.party_id == party_id)))
    for session in sessions:
        if session.document_file_path:
            try:
                delete_identity_document(db, session.document_file_path)
            except (OSError, ValueError):
                pass
        session.document_file_path = None
        session.encrypted_reference = None
        session.provider_session_url_encrypted = None
        session.legal_name_snapshot = ""
        session.extracted_name = None
        session.evidence_purged_at = _now()
    profile = get_profile(db, party_id)
    if profile is not None:
        if profile.state == "VERIFIED":
            mark_reverification_required(db, profile, "EVIDENCE_INVALIDATED", correlation_id=correlation_id)
        profile.given_name = profile.middle_names = profile.family_name = profile.verified_legal_name = ""
        profile.date_of_birth = None
        profile.state = "NOT_STARTED"
        profile.assurance_level = "IV-0"
        profile.current_verification_id = None
        profile.updated_at = _now()
        emit_event(db, "IDENTITY_DATA_ERASED", "identity_verification", f"profile:{profile.id}",
                   {"partyId": party_id, "sessions": len(sessions)},
                   correlation_id=correlation_id, actor_kind="admin", actor_id=str(admin.id))
    db.commit()
    return {"sessions": len(sessions)}


# -- retention (Section 9.2) ----------------------------------------------------

_DECIDED_STATES = ("VERIFIED", "FAILED", "REVERIFICATION_REQUIRED", "ACTION_REQUIRED")


def purge_expired_evidence(db: Session) -> int:
    """Scheduled job: deletes raw evidence files whose country pack retention
    period after the decision has passed. The decision, masked number and
    hashes are kept; nothing is purged while undecided or under a pack
    without a retention period."""
    from app.core.identity_uploads import delete_identity_document

    purged = 0
    now = _now()
    candidates = db.scalars(select(IdentityVerification).where(
        IdentityVerification.session_state.in_(_DECIDED_STATES),
        IdentityVerification.document_file_path.is_not(None),
        IdentityVerification.evidence_purged_at.is_(None),
        IdentityVerification.decided_at.is_not(None),
    ))
    for session in candidates:
        days = policy.get_pack(db, session.country_code).evidence_retention_days
        decided = _as_utc(session.decided_at)
        if not days or decided is None or decided + timedelta(days=days) > now:
            continue
        try:
            delete_identity_document(db, session.document_file_path)
        except (OSError, ValueError):
            logger.warning("identity_verification #%s: could not delete evidence file", session.id)
            continue
        session.document_file_path = None
        session.evidence_purged_at = now
        purged += 1
    db.commit()
    return purged


# -- operational metrics (Section 15) -------------------------------------------

def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return round(ordered[index], 1)


def metrics(db: Session, *, days: int = 30) -> dict:
    """Aggregates only -- counts, rates and durations by method / safe reason
    code. No names, numbers, dates of birth or images."""
    since = _now() - timedelta(days=days)
    sessions = list(db.scalars(select(IdentityVerification).where(
        IdentityVerification.created_at >= since,
        IdentityVerification.session_state != "IN_PROGRESS",
    )))
    started = list(db.scalars(select(IdentityVerification).where(IdentityVerification.created_at >= since)))
    submitted = [s for s in sessions if s.submitted_at is not None]
    verified = [s for s in submitted if s.session_state in ("VERIFIED", "REVERIFICATION_REQUIRED")]

    def hours(s: IdentityVerification) -> float | None:
        start, end = _as_utc(s.submitted_at), _as_utc(s.decided_at)
        return (end - start).total_seconds() / 3600 if start and end else None

    decision_times = [h for s in submitted if (h := hours(s)) is not None]

    reason_counts: dict[str, int] = {}
    for s in sessions:
        for code in s.reason_codes or []:
            reason_counts[code] = reason_counts.get(code, 0) + 1

    by_method: dict[str, dict] = {}
    for s in submitted:
        row = by_method.setdefault(s.method_type or "UNKNOWN", {"submitted": 0, "verified": 0})
        row["submitted"] += 1
        if s in verified:
            row["verified"] += 1

    # Action-required recovery: people who hit ACTION_REQUIRED and are now verified.
    action_parties = {s.party_id for s in sessions if "ACTION_REQUIRED" in (s.status or "").upper()
                      or s.session_state == "ACTION_REQUIRED" or s.normalized_outcome == "ACTION_REQUIRED"}
    recovered = {s.party_id for s in verified if s.party_id in action_parties}
    provider_errors = sum(1 for s in started if "PROVIDER_UNAVAILABLE" in (s.reason_codes or []))  # face models missing

    def rate(part: int, whole: int) -> float | None:
        return round(100 * part / whole, 1) if whole else None

    return {
        "period_days": days,
        "started": len(started),
        "submitted": len(submitted),
        "verified": len(verified),
        "start_to_verified_rate": rate(len(verified), len(started)),
        "action_required_recovery_rate": rate(len(recovered), len(action_parties)),
        "provider_error_rate": rate(provider_errors, len(started)),
        "time_to_decision_hours": {"median": _percentile(decision_times, 50), "p90": _percentile(decision_times, 90)},
        "by_method": by_method,
        "reason_codes": dict(sorted(reason_counts.items(), key=lambda kv: -kv[1])),
    }


# -- the face match (the whole check) ---------------------------------------------------------------------------

_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_MIN_PHOTO_BYTES = 5 * 1024


def _photo_type(content: bytes) -> str | None:
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def capture_open(session: IdentityVerification) -> bool:
    """Whether the person can submit their photos now: they confirmed the details, and the verification is in
    progress or was sent back for clearer photos."""
    return session.attested_at is not None and session.session_state in OPEN_SESSION_STATES


def _open_capture(db: Session, session: IdentityVerification, profile: IdentityProfile, pack, *,
                  correlation_id: str = "") -> IdentityVerification:
    """Record the attestation (what they confirmed, which notice they saw) and open the photo step."""
    session.attested_at = _now()
    session.submitted_at = session.submitted_at or _now()
    session.legal_name_snapshot = profile.legal_name
    session.country_code = profile.country_code
    session.consent_notice_version = policy.consent_notice_version(pack)
    session.provider_code = providers.FACE_MATCH
    session.reason_codes = []
    _set_profile_state(profile, "IN_PROGRESS")
    _event(db, "IDENTITY_METHOD_SELECTED", profile, session, actor_kind="user", correlation_id=correlation_id)
    db.commit()
    db.refresh(session)
    return session


def _face_result(comparison) -> providers.NormalizedResult:
    """Face comparison -> normalized outcome. At or above the pass line: verified. Clearly different: declined.
    Anything unclear (no face found, poor photo, in between): ask for better photos -- never a pass."""
    base = dict(provider_code=providers.FACE_MATCH, method_type="DOCUMENT")
    if comparison.score is None:
        return providers.NormalizedResult(
            normalized_outcome="ACTION_REQUIRED", reason_codes=list(comparison.issues) or ["RESUBMISSION_REQUESTED"],
            provider_decision="resubmission_requested", provider_status="not_compared", **base)
    score = comparison.score  # decide on the exact score; rounding is only for what is stored
    results = {"person_document_binding": "NOT_CHECKED", "document_authenticity": "NOT_CHECKED",
               "liveness_or_alternative_binding": "NOT_CHECKED", "face_match_score": round(score, 3)}
    if score >= settings.face_match_pass:
        return providers.NormalizedResult(
            normalized_outcome="PASS", provider_decision="approved", provider_status="approved",
            match_results={**results, "person_document_binding": "PASS"}, **base)
    if score < settings.face_match_fail:
        return providers.NormalizedResult(
            normalized_outcome="FAIL", reason_codes=["PROVIDER_DECLINED"], provider_decision="declined",
            provider_status="declined", match_results={**results, "person_document_binding": "FAIL"}, **base)
    return providers.NormalizedResult(
        normalized_outcome="ACTION_REQUIRED", reason_codes=["FACE_MATCH_UNSURE"],
        provider_decision="resubmission_requested", provider_status="unsure", match_results=results, **base)


def verify_faces(db: Session, user: UserAccount, session: IdentityVerification, *, document_photo: bytes,
                 selfie: bytes, document_type: str, correlation_id: str = "") -> IdentityVerification:
    """THE identity check: compare the face on the uploaded document photo with the selfie, on this server. If they
    match, the identity is verified. The photos are compared in memory and never stored.

    Only the match is checked: not that the document is genuine, in date, or in the person's name, and there is no
    liveness test (see services/identity/face_match.py). The decision is made here, synchronously, from the
    server's own comparison -- nothing a client sends can mark a person verified."""
    from app.services.identity import face_match

    if not capture_open(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "This verification isn't taking photos right now")
    for content in (document_photo, selfie):
        if len(content) > _MAX_PHOTO_BYTES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A photo is too large. Try again with your camera.")
        if _photo_type(content) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use JPG or PNG photos taken with your camera.")
        if len(content) < _MIN_PHOTO_BYTES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, describe(["DOCUMENT_UNREADABLE"])["message"])
    profile = get_or_create_profile(db, session.party_id)
    pack = policy.get_pack(db, profile.country_code)
    if document_type not in (pack.accepted_document_types or []):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This document can't be used for this verification. Choose another accepted identity document.",
        )
    try:
        comparison = face_match.compare_faces(document_photo, selfie)
    except face_match.FaceMatchUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, describe(["PROVIDER_UNAVAILABLE"])["message"])

    from app.models.identity_verification import DOCUMENT_CATEGORY_BY_TYPE

    if session.session_state == "ACTION_REQUIRED":  # a retry after being asked for clearer photos
        _transition(session, "IN_PROGRESS")
        session.reason_codes = []
        _set_profile_state(profile, "IN_PROGRESS")
    session.document_type = document_type
    session.document_category = DOCUMENT_CATEGORY_BY_TYPE.get(document_type, "identity")
    session.submitted_at = _now()
    _transition(session, "PROCESSING")
    _set_profile_state(profile, "PROCESSING")
    _event(db, "IDENTITY_VERIFICATION_SUBMITTED", profile, session, actor_kind="user", actor_id=str(user.id),
           previous_state="IN_PROGRESS", new_state="PROCESSING", correlation_id=correlation_id)
    db.flush()
    return apply_result(db, session, _face_result(comparison), correlation_id=correlation_id)
