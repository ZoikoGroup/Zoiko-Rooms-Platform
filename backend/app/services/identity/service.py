"""ZR-IDENTITY-001 orchestration: the account-level identity profile, its
verification sessions, provider results, reviewer decisions and
re-verification. Server-authoritative: nothing a client sends can mark a
person verified (Section 8.4) -- only a provider PASS or a reviewer APPROVE.

Flow (Section 4): start_session -> attach_document -> submit (attested) ->
provider -> VERIFIED / PENDING_REVIEW / ACTION_REQUIRED / FAILED.
"""

from __future__ import annotations

import hashlib
import json
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
from app.models.identity_profile import (
    ASSURANCE_LEVELS, IdentityProfile, IdentityProviderEvent, ROLE_CONTEXTS, VERIFICATION_METHODS,
)
from app.models.identity_verification import DOCUMENT_CATEGORY_BY_TYPE, IDENTITY_DOCUMENT_TYPES, IdentityVerification
from app.models.user_account import UserAccount
from app.models.verification_credential import VerificationCredential
from app.services.identity import policy, providers
from app.services.identity.reason_codes import ALTERNATIVE_REASON_CODES, REVIEWER_REASON_CODES, describe

logger = logging.getLogger("uvicorn.error")

# Section 7.1 transitions a verification session may make.
_SESSION_TRANSITIONS: dict[str, set[str]] = {
    "IN_PROGRESS": {"PROCESSING", "PENDING_REVIEW", "IN_PROGRESS"},
    "PROCESSING": {"VERIFIED", "PENDING_REVIEW", "ACTION_REQUIRED", "FAILED", "IN_PROGRESS"},
    "PENDING_REVIEW": {"VERIFIED", "ACTION_REQUIRED", "FAILED", "PENDING_REVIEW"},
    "ACTION_REQUIRED": {"IN_PROGRESS", "PENDING_REVIEW", "FAILED"},
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
_ALTERNATIVE_DAILY_LIMIT = 3
# Section 2.3: whichever provider confirms authenticity + binding gives IV-1.
# The built-in check confirms neither, so its pass is IV-1 only in the
# sense of "the platform's standard gate", recorded honestly in
# match_results; a manual approval after review is IV-2.
_PROVIDER_PASS_LEVEL = "IV-1"
_REVIEWED_LEVEL = "IV-2"


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


def attach_document(db: Session, user: UserAccount, session: IdentityVerification, *, document_type: str,
                    document_number: str, stored_filename: str, original_filename: str, content_type: str,
                    file_size: int, duplicate_of_verification_id: int | None = None,
                    correlation_id: str = "") -> IdentityVerification:
    profile = get_or_create_profile(db, session.party_id)
    if session.session_state == "ACTION_REQUIRED":
        _transition(session, "IN_PROGRESS")
    if session.session_state != "IN_PROGRESS":
        raise HTTPException(status.HTTP_409_CONFLICT, "This verification can't take a new document now")
    pack = policy.get_pack(db, profile.country_code)
    if document_type not in IDENTITY_DOCUMENT_TYPES or document_type not in (pack.accepted_document_types or []):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This document can't be used for this verification. Choose another accepted identity document.",
        )
    from app.core.field_encryption import encrypt_text

    number = document_number.strip()
    session.document_type = document_type
    session.document_category = DOCUMENT_CATEGORY_BY_TYPE[document_type]
    session.encrypted_reference = encrypt_text(number) if number else None
    session.masked_document_number = providers.mask_number(number)
    session.document_number_hash = providers.number_hash(document_type, number)
    session.document_file_path = stored_filename
    session.document_file_original_name = original_filename
    session.document_file_content_type = content_type
    session.document_file_size = file_size
    session.match_results = {**(session.match_results or {}), "duplicate_of_verification_id": duplicate_of_verification_id}
    session.updated_at = _now()
    _event(db, "IDENTITY_EVIDENCE_CAPTURED", profile, session, actor_kind="user", actor_id=str(user.id),
           correlation_id=correlation_id)
    db.commit()
    db.refresh(session)
    return session


def submit(db: Session, user: UserAccount, session: IdentityVerification, *, attested: bool,
           correlation_id: str = "") -> IdentityVerification:
    """Section 5.6 review & attest, then automated checks."""
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
    from app.services.identity.golive import resolve_provider

    provider_code = providers.ManualReviewProvider.code if session.method_type == "MANUAL" else pack.document_provider_code
    provider = resolve_provider(db, provider_code)
    hosted = provider is not None and provider.capture_mode == providers.PROVIDER_HOSTED
    if provider is None:
        # Configured provider not reachable / not configured: keep progress.
        session.attested_at = _now()
        return _provider_unavailable(db, session, profile, correlation_id)
    if session.method_type == "DOCUMENT" and not hosted and not session.document_file_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add your identity document before submitting")
    if pack.minimum_age and profile.date_of_birth and policy.age_on(profile.date_of_birth) < pack.minimum_age:
        pass  # decided below, after the attempt is recorded
    elif hosted:
        return _launch_hosted(db, session, profile, pack, provider, correlation_id=correlation_id)

    session.attested_at = _now()
    session.submitted_at = _now()
    session.legal_name_snapshot = profile.legal_name
    session.country_code = profile.country_code
    _transition(session, "PROCESSING")
    _set_profile_state(profile, "PROCESSING")
    db.flush()

    if pack.minimum_age and profile.date_of_birth and policy.age_on(profile.date_of_birth) < pack.minimum_age:
        result = providers.NormalizedResult(
            provider_code="zoiko_policy", normalized_outcome="FAIL", reason_codes=["AGE_REQUIREMENT_NOT_MET"],
        )
        return apply_result(db, session, result, correlation_id=correlation_id)

    if provider is None:
        return _provider_unavailable(db, session, profile, correlation_id)
    session.provider_code = provider.code
    try:
        result = provider.evaluate(_evidence(db, session, profile))
    except providers.ProviderUnavailable:
        return _provider_unavailable(db, session, profile, correlation_id)
    if (result.normalized_outcome == "PASS" and provider.code == providers.DocumentCheckProvider.code
            and not settings.builtin_check_can_verify):
        # The built-in check can't confirm authenticity or that it's the
        # person's own document: in production a reviewer decides.
        result.normalized_outcome = "REVIEW"
        result.reason_codes = ["AUTOMATED_CHECK_INSUFFICIENT"]
    if not result.normalized_outcome:
        # Asynchronous provider: the result arrives by webhook.
        session.provider_session_id = result.provider_session_id
        db.commit()
        db.refresh(session)
        return session
    return apply_result(db, session, result, correlation_id=correlation_id)


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


def _evidence(db: Session, session: IdentityVerification, profile: IdentityProfile) -> providers.Evidence:
    from app.core.field_encryption import decrypt_text
    from app.core.identity_uploads import resolve_identity_document_path

    document_bytes = b""
    if session.document_file_path:
        try:
            document_bytes = resolve_identity_document_path(session.document_file_path).read_bytes()
        except OSError:
            logger.warning("identity_verification #%s: stored document unreadable", session.id)
    used_elsewhere = bool(session.document_number_hash) and db.scalar(
        select(func.count(IdentityVerification.id)).where(
            IdentityVerification.document_number_hash == session.document_number_hash,
            IdentityVerification.party_id != session.party_id,
            IdentityVerification.session_state.in_(("VERIFIED", "PENDING_REVIEW", "REVERIFICATION_REQUIRED")),
        )
    ) > 0
    return providers.Evidence(
        document_type=session.document_type, document_bytes=document_bytes,
        content_type=session.document_file_content_type, typed_number=decrypt_text(session.encrypted_reference),
        legal_name=profile.legal_name, country_code=profile.country_code,
        duplicate_of_verification_id=(session.match_results or {}).get("duplicate_of_verification_id"),
        number_used_by_other_account=used_elsewhere,
    )


# States a provider decision may still change. Anything else is final for
# this attempt (ZR-IDV-ADR-001 Section 13: never regress VERIFIED).
_DECIDABLE_BY_PROVIDER = ("IN_PROGRESS", "PROCESSING", "PENDING_REVIEW", "ACTION_REQUIRED")


def apply_result(db: Session, session: IdentityVerification, result: providers.NormalizedResult, *,
                 correlation_id: str = "") -> IdentityVerification:
    """Maps a normalized provider outcome onto the session and profile.
    Only a normalized PASS can verify."""
    profile = get_or_create_profile(db, session.party_id)
    if session.session_state not in _DECIDABLE_BY_PROVIDER:
        if session.session_state == "VERIFIED" and result.normalized_outcome == "FAIL":
            # The provider later invalidated evidence it had passed (Section 7.3).
            mark_reverification_required(db, profile, "EVIDENCE_INVALIDATED", correlation_id=correlation_id)
            db.commit()
        return session
    if session.session_state == "ACTION_REQUIRED":
        _transition(session, "IN_PROGRESS")  # a resubmission came back
    if session.session_state == "IN_PROGRESS":
        _transition(session, "PROCESSING")
    if result.provider_reason_code:
        from app.services.identity.golive import map_reason

        mapped = map_reason(db, result.provider_code, result.provider_decision, result.provider_reason_code)
        if mapped:
            result.reason_codes = [mapped]
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
    if result.document_metadata.get("masked_document_number") and not session.masked_document_number:
        session.masked_document_number = result.document_metadata["masked_document_number"]
    session.ocr_extracted_number = result.document_metadata.get("masked_document_number") or session.ocr_extracted_number
    _event(db, "IDENTITY_PROVIDER_RESULT_RECEIVED", profile, session, reason_codes=result.reason_codes,
           correlation_id=correlation_id)

    outcome = result.normalized_outcome
    if outcome == "PASS":
        _verify(db, session, profile, assurance=_PROVIDER_PASS_LEVEL, correlation_id=correlation_id,
                verified_attributes=result.verified_attributes)
    elif outcome == "REVIEW" and result.provider_reviewing:
        # The provider's own reviewers are looking: still "checking", and it
        # never lands in the Zoiko review queue (the provider decides).
        if session.session_state != "PROCESSING":
            _transition(session, "PROCESSING")
        _set_profile_state(profile, "PROCESSING", result.reason_codes)
    elif outcome == "REVIEW":
        _to_review(db, session, profile, result.reason_codes, correlation_id=correlation_id)
    elif outcome == "ACTION_REQUIRED":
        _to_action_required(db, session, profile, result.reason_codes, correlation_id=correlation_id)
    else:
        _to_failed(db, session, profile, result.reason_codes, correlation_id=correlation_id)
    db.commit()
    db.refresh(session)
    return session


def _verify(db: Session, session: IdentityVerification, profile: IdentityProfile, *, assurance: str,
            admin: AdminUser | None = None, note: str = "", correlation_id: str = "",
            verified_attributes: dict | None = None) -> None:
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
    session.verifier_admin_id = admin.id if admin else None
    session.verifier_notes = note
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
           actor_kind="admin" if admin else "system", actor_id=str(admin.id) if admin else "",
           previous_state=previous, new_state="VERIFIED", correlation_id=correlation_id)

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


def _to_review(db: Session, session: IdentityVerification, profile: IdentityProfile, reason_codes: list[str], *,
               correlation_id: str = "") -> None:
    previous = profile.state
    _transition(session, "PENDING_REVIEW")
    session.assurance_level = _REVIEWED_LEVEL
    _set_profile_state(profile, "PENDING_REVIEW", reason_codes)
    _event(db, "IDENTITY_MANUAL_REVIEW_STARTED", profile, session, reason_codes=reason_codes,
           previous_state=previous, new_state="PENDING_REVIEW", correlation_id=correlation_id)
    user = _user_for_party(db, session.party_id)
    who = user.full_name if user else f"Party #{session.party_id}"
    message = f"{who}'s identity verification needs a reviewer ({', '.join(reason_codes) or 'manual review'})."
    duplicate_of = (session.match_results or {}).get("duplicate_of_verification_id")
    if duplicate_of:
        # Reviewer-only context -- never shown to the person.
        message += f" Its document matches verification #{duplicate_of}; check for reuse."
    notif_crud.notify_all_super_admins(
        db, title="Identity verification pending review",
        message=message,
        notification_type="identity_verification.submitted",
        related_entity_type="identity_verification", related_entity_id=str(session.id),
    )


def _to_action_required(db: Session, session: IdentityVerification, profile: IdentityProfile, reason_codes: list[str],
                        *, admin: AdminUser | None = None, note: str = "", correlation_id: str = "") -> None:
    previous = profile.state
    _transition(session, "ACTION_REQUIRED")
    session.decided_at = _now()
    if admin is not None:
        session.verifier_admin_id = admin.id
    message = note or describe(reason_codes)["message"]
    session.verifier_notes = message
    _set_profile_state(profile, "ACTION_REQUIRED", reason_codes)
    _event(db, "IDENTITY_ACTION_REQUIRED", profile, session, reason_codes=reason_codes,
           actor_kind="admin" if admin else "system", actor_id=str(admin.id) if admin else "",
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
               admin: AdminUser | None = None, note: str = "", correlation_id: str = "") -> None:
    previous = profile.state
    _transition(session, "FAILED")
    session.decided_at = _now()
    if admin is not None:
        session.verifier_admin_id = admin.id
    message = describe(reason_codes)["message"]
    session.verifier_notes = message
    _set_profile_state(profile, "FAILED", reason_codes)
    _event(db, "IDENTITY_VERIFICATION_FAILED", profile, session, reason_codes=reason_codes,
           actor_kind="admin" if admin else "system", actor_id=str(admin.id) if admin else "",
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


# -- alternative route, resume, phone handoff ----------------------------------

def request_alternative(db: Session, user: UserAccount, session: IdentityVerification, *, reason_code: str,
                        note: str = "", correlation_id: str = "") -> IdentityVerification:
    """Section 5.3/11: a manual route instead of a dead end. Rate-limited."""
    if reason_code not in ALTERNATIVE_REASON_CODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reason must be one of {ALTERNATIVE_REASON_CODES}")
    recent = db.scalar(select(func.count(IdentityVerification.id)).where(
        IdentityVerification.party_id == session.party_id,
        IdentityVerification.alternative_reason != "",
        IdentityVerification.updated_at >= _now() - timedelta(days=1),
    )) or 0
    if recent >= _ALTERNATIVE_DAILY_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many review requests today -- please try again tomorrow")
    profile = get_or_create_profile(db, session.party_id)
    if session.session_state not in OPEN_SESSION_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "This verification can't be moved to manual review now")
    if session.session_state == "ACTION_REQUIRED":
        _transition(session, "IN_PROGRESS")
    session.method_type = "MANUAL"
    session.alternative_reason = reason_code
    session.provider_code = providers.ManualReviewProvider.code
    session.legal_name_snapshot = profile.legal_name
    session.submitted_at = _now()
    session.verifier_notes = note.strip()[:2000]
    _to_review(db, session, profile, ["ALTERNATIVE_REQUESTED"], correlation_id=correlation_id)
    db.commit()
    db.refresh(session)
    return session


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


# -- reviewer decisions (Section 13) -------------------------------------------

REVIEWER_DECISIONS = ("APPROVE", "ACTION_REQUIRED", "REJECT", "ESCALATE")


def reviewer_decision(db: Session, admin: AdminUser, session: IdentityVerification, *, decision: str,
                      reason_code: str, note: str, correlation_id: str = "") -> IdentityVerification:
    """Approve / Action required / Reject / Escalate. A reason code and a
    reviewer note are required for every manual decision; the decision is
    recorded as an immutable IDENTITY_REVIEWER_DECISION event with the
    before/after state."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    decision = decision.upper()
    if decision not in REVIEWER_DECISIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"decision must be one of {REVIEWER_DECISIONS}")
    if reason_code not in REVIEWER_REASON_CODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reasonCode must be one of {REVIEWER_REASON_CODES}")
    if not note.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add a reviewer note explaining the decision")
    profile = get_or_create_profile(db, session.party_id)
    if session.session_state not in ("PENDING_REVIEW", "PROCESSING", "ACTION_REQUIRED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This verification is {session.session_state} and can't be decided")
    before = session.session_state

    if decision == "APPROVE":
        if session.session_state == "ACTION_REQUIRED":
            _transition(session, "PENDING_REVIEW")
        _verify(db, session, profile, assurance=_REVIEWED_LEVEL, admin=admin, note=note.strip(),
                correlation_id=correlation_id)
    elif decision == "ACTION_REQUIRED":
        if session.session_state == "ACTION_REQUIRED":
            raise HTTPException(status.HTTP_409_CONFLICT, "Action is already required on this verification")
        _to_action_required(db, session, profile, [reason_code], admin=admin, note=note.strip(),
                            correlation_id=correlation_id)
    elif decision == "REJECT":
        if session.session_state == "ACTION_REQUIRED":
            _transition(session, "PENDING_REVIEW")
        _to_failed(db, session, profile, [reason_code if reason_code != "REVIEWER_APPROVED" else "REVIEWER_REJECTED"],
                   admin=admin, note=note.strip(), correlation_id=correlation_id)
    else:  # ESCALATE -- stays in review at the enhanced level, flagged for a senior reviewer
        if session.session_state != "PENDING_REVIEW":
            raise HTTPException(status.HTTP_409_CONFLICT, "Only a verification in review can be escalated")
        session.escalated_at = _now()
        session.assurance_level = _REVIEWED_LEVEL
        session.reason_codes = list(dict.fromkeys([*(session.reason_codes or []), "ESCALATED"]))
        notif_crud.notify_all_super_admins(
            db, title="Identity verification escalated",
            message=f"Identity verification #{session.id} was escalated for a senior review: {note.strip()[:500]}",
            notification_type="identity_verification.escalated",
            related_entity_type="identity_verification", related_entity_id=str(session.id),
        )
    session.verifier_admin_id = admin.id
    if decision != "APPROVE":
        session.verifier_notes = note.strip()[:2000]
    emit_event(
        db, "IDENTITY_REVIEWER_DECISION", "identity_verification", str(session.id),
        {"partyId": session.party_id, "decision": decision, "reasonCode": reason_code},
        correlation_id=correlation_id, actor_kind="admin", actor_id=str(admin.id),
        previous_state=before, new_state=session.session_state,
    )
    db.commit()
    db.refresh(session)
    return session


# -- hosted provider sessions (ZR-IDV-ADR-001 Section 6) ------------------------

def _launch_hosted(db: Session, session: IdentityVerification, profile: IdentityProfile, pack,
                   provider: providers.IdentityProvider, *, correlation_id: str = "") -> IdentityVerification:
    """Steps 1-4: record the attestation, create the provider session
    server-side with opaque correlation ids only, persist the provider
    session id and (encrypted) launch URL. The session stays IN_PROGRESS
    until the person submits inside the provider's flow."""
    import uuid

    from app.core.config import settings
    from app.core.field_encryption import encrypt_text

    session.attested_at = _now()
    session.submitted_at = session.submitted_at or _now()
    session.legal_name_snapshot = profile.legal_name
    session.country_code = profile.country_code
    session.consent_notice_version = policy.consent_notice_version(pack)
    session.provider_code = provider.code
    if not profile.provider_subject_reference:
        profile.provider_subject_reference = str(uuid.uuid4())  # opaque endUserId, no personal data
    db.flush()
    if not session.provider_session_id:
        try:
            created = provider.create_session(
                vendor_data=f"zr-idv-{session.id}", end_user_id=profile.provider_subject_reference,
                callback_url=f"{settings.frontend_url.rstrip('/')}/account/identity?verification=returned",
            )
        except providers.ProviderUnavailable:
            session.reason_codes = ["PROVIDER_UNAVAILABLE"]
            _set_profile_state(profile, "IN_PROGRESS", ["PROVIDER_UNAVAILABLE"])
            _event(db, "IDENTITY_PROVIDER_RESULT_RECEIVED", profile, session, reason_codes=["PROVIDER_UNAVAILABLE"],
                   correlation_id=correlation_id)
            db.commit()
            db.refresh(session)
            return session
        session.provider_session_id = created.provider_session_id
        session.provider_session_url_encrypted = encrypt_text(created.launch_url)
    session.reason_codes = []
    _set_profile_state(profile, "IN_PROGRESS")
    _event(db, "IDENTITY_METHOD_SELECTED", profile, session, actor_kind="user", correlation_id=correlation_id)
    db.commit()
    db.refresh(session)
    return session


def launch_url(session: IdentityVerification) -> str | None:
    """The provider capture URL, only while the person can still capture
    (in progress, or the provider asked for a resubmission)."""
    from app.core.field_encryption import decrypt_text

    if not session.provider_session_url_encrypted:
        return None
    if session.session_state not in ("IN_PROGRESS", "ACTION_REQUIRED"):
        return None
    if session.session_state == "ACTION_REQUIRED" and session.provider_decision != "resubmission_requested":
        return None
    return decrypt_text(session.provider_session_url_encrypted)


_RESTARTABLE = ("SESSION_EXPIRED", "SESSION_ABANDONED", "PROVIDER_UNAVAILABLE", "PROVIDER_DECLINED")


def restart(db: Session, user: UserAccount, session: IdentityVerification, *, correlation_id: str = "") -> IdentityVerification:
    """ZR-IDV-ADR-001 Section 10 /restart: a new attempt after an expired,
    abandoned or declined one, within the pack's daily attempt limit."""
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
        raise HTTPException(status.HTTP_409_CONFLICT, "This verification can't be restarted")
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
    return start_session(db, user, method=session.method_type if session.method_type != "MANUAL" else "DOCUMENT",
                         role_context=session.role_context, correlation_id=correlation_id)


def reconcile(db: Session, session: IdentityVerification, *, correlation_id: str = "") -> str:
    """Asks the provider for the decision of one session (never the
    browser). Returns what happened."""
    provider = providers.get_provider(session.provider_code)
    if provider is None or provider.capture_mode != providers.PROVIDER_HOSTED or not session.provider_session_id:
        return "not_applicable"
    try:
        result = provider.get_decision(session.provider_session_id)
    except providers.ProviderUnavailable:
        return "provider_unavailable"
    if result is None:
        return "no_decision_yet"
    session.match_results = {**(session.match_results or {}), "reconciled_at": _now().isoformat()}
    apply_result(db, session, result, correlation_id=correlation_id)
    return "applied"


_USER_REFRESH_INTERVAL = timedelta(seconds=30)


def refresh_from_provider(db: Session, user: UserAccount, session: IdentityVerification) -> IdentityVerification:
    """The person asks "is my result in yet?" after finishing the provider's
    flow: the server asks the provider's decision API (never trusting the
    browser). At most once every 30 seconds per session; outside capture
    and checking this simply returns the current status."""
    if session.party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own identity verifications")
    if session.session_state not in ("IN_PROGRESS", "PROCESSING") or not session.provider_session_url_encrypted:
        return session
    last = (session.match_results or {}).get("user_refreshed_at")
    if last and datetime.fromisoformat(last) > _now() - _USER_REFRESH_INTERVAL:
        return session
    session.match_results = {**(session.match_results or {}), "user_refreshed_at": _now().isoformat()}
    db.commit()
    reconcile(db, session)
    db.refresh(session)
    return session


def reconcile_stale_sessions(db: Session) -> int:
    """Scheduled job: hosted sessions with no decision after the configured
    threshold are reconciled against the provider's decision API."""
    from app.core.config import settings

    cutoff = _now() - timedelta(minutes=settings.veriff_reconcile_after_minutes)
    stale = list(db.scalars(select(IdentityVerification).where(
        IdentityVerification.session_state.in_(("IN_PROGRESS", "PROCESSING")),
        IdentityVerification.provider_session_id != "",
        IdentityVerification.provider_session_url_encrypted.is_not(None),
        IdentityVerification.updated_at <= cutoff,
    )))
    applied = 0
    for session in stale:
        if reconcile(db, session) == "applied":
            applied += 1
        else:
            session.updated_at = _now()  # back off until the next threshold
            db.commit()
    return applied


# -- provider webhooks (ZR-IDENTITY-001 Section 8.3; ZR-IDV-ADR-001 Section 8) ----

def ingest_webhook(db: Session, provider_code: str, headers: dict[str, str], body: bytes, *,
                   kind: str = "decision", correlation_id: str = "") -> dict:
    """Authenticate first (on the exact raw bytes), then durably record each
    event, then process it. Delivery is at-least-once and unordered: a
    duplicate is acknowledged and not reprocessed; an event that fails to
    process stays "accepted" for the sweeper (process_pending_webhook_events)
    and the delivery is still acknowledged."""
    from app.core.field_encryption import encrypt_text

    provider = providers.get_provider(provider_code)
    if provider is None or not provider.supports_webhooks:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown identity provider")
    digest = hashlib.sha256(body).hexdigest()
    if not provider.verify_webhook(headers, body):
        db.add(IdentityProviderEvent(
            provider_code=provider.code, provider_event_id=f"rejected:{secrets.token_hex(8)}",
            event_type=f"{kind}.auth_failed", payload_sha256=digest, status="rejected",
        ))
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")
    try:
        events = provider.parse_webhook(body, kind)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed webhook body")

    accepted, duplicates, processed = 0, 0, 0
    for event in events:
        if not event.provider_event_id:
            continue
        existing = db.scalar(select(IdentityProviderEvent).where(
            IdentityProviderEvent.provider_code == provider.code,
            IdentityProviderEvent.provider_event_id == event.provider_event_id,
        ))
        if existing is not None:
            existing.duplicate_count += 1
            db.commit()
            duplicates += 1
            continue
        session = _session_for_event(db, provider.code, event)
        row = IdentityProviderEvent(
            provider_code=provider.code, provider_event_id=event.provider_event_id,
            identity_verification_id=session.id if session else None, event_type=event.event_type,
            payload_sha256=digest, status="accepted",
            payload_encrypted=encrypt_text(json.dumps({
                "event_type": event.event_type, "provider_session_id": event.provider_session_id,
                "result": event.result.to_json() if event.result else None,
            })),
        )
        db.add(row)
        db.commit()
        accepted += 1
        if _process_event_row(db, row, correlation_id=correlation_id):
            processed += 1
    return {"accepted": accepted, "duplicates": duplicates, "processed": processed}


def _session_for_event(db: Session, provider_code: str, event: providers.WebhookEvent) -> IdentityVerification | None:
    if not event.provider_session_id:
        return None
    session = db.scalar(select(IdentityVerification).where(
        IdentityVerification.provider_code == provider_code,
        IdentityVerification.provider_session_id == event.provider_session_id,
    ))
    # vendorData is our opaque attempt reference: it must agree when present.
    if session is not None and event.vendor_data and event.vendor_data != f"zr-idv-{session.id}":
        logger.warning("identity webhook: vendorData does not match attempt #%s -- ignored", session.id)
        return None
    return session


def _process_event_row(db: Session, row: IdentityProviderEvent, *, correlation_id: str = "") -> bool:
    from app.core.field_encryption import decrypt_text

    row.attempts += 1
    try:
        data = json.loads(decrypt_text(row.payload_encrypted)) if row.payload_encrypted else {}
        session = db.get(IdentityVerification, row.identity_verification_id) if row.identity_verification_id else None
        if session is None:
            row.status, row.processing_error = "ignored", "no matching verification"
        elif row.event_type.startswith("progress."):
            # Progress only -- an event webhook can never verify anyone.
            if row.event_type == "progress.submitted" and session.session_state == "IN_PROGRESS":
                profile = get_or_create_profile(db, session.party_id)
                _transition(session, "PROCESSING")
                _set_profile_state(profile, "PROCESSING")
            row.status = "processed"
        elif data.get("result"):
            apply_result(db, session, providers.NormalizedResult.from_json(data["result"]), correlation_id=correlation_id)
            row.status = "processed"
        else:
            row.status = "ignored"
        row.processed_at = _now()
        row.payload_encrypted = None  # minimum retention: drop the content once applied
        db.commit()
        return row.status == "processed"
    except Exception as exc:  # stays "accepted" for the sweeper
        db.rollback()
        logger.exception("identity webhook event #%s failed to process", row.id)
        row = db.get(IdentityProviderEvent, row.id)
        if row is not None:
            row.processing_error = type(exc).__name__[:300]
            row.attempts = (row.attempts or 0) + 1
            if row.attempts >= 10:
                row.status = "failed"
            db.commit()
        return False


def process_pending_webhook_events(db: Session) -> int:
    """Scheduled job: retry accepted-but-unprocessed webhook events."""
    rows = list(db.scalars(select(IdentityProviderEvent).where(IdentityProviderEvent.status == "accepted")
                           .order_by(IdentityProviderEvent.received_at)))
    return sum(1 for row in rows if _process_event_row(db, row))


def process_webhook(db: Session, provider_code: str, headers: dict[str, str], body: bytes,
                    correlation_id: str = "") -> dict:
    """Generic signed-webhook provider entry point (same ingest pipeline)."""
    return ingest_webhook(db, provider_code, headers, body, kind="decision", correlation_id=correlation_id)


# -- privacy: erasure across Zoiko and the provider (ZR-IDV-ADR-001 Section 12) --

def erase_identity_data(db: Session, admin: AdminUser, party_id: int, *, reason: str, correlation_id: str = "") -> dict:
    """A lawful deletion request: removes stored evidence, document numbers,
    names and dates of birth on Zoiko's side, asks the provider to delete its
    sessions, and leaves the person unverified. Decisions and the audit trail
    (which hold no identity content) are kept."""
    from app.core.identity_uploads import resolve_identity_document_path

    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the reason for the erasure request")
    sessions = list(db.scalars(select(IdentityVerification).where(IdentityVerification.party_id == party_id)))
    provider_deleted = provider_failed = 0
    for session in sessions:
        if session.document_file_path:
            try:
                path = resolve_identity_document_path(session.document_file_path)
                if path.is_file():
                    path.unlink()
            except (OSError, ValueError):
                pass
        provider = providers.get_provider(session.provider_code) if session.provider_session_id else None
        if provider is not None and provider.capture_mode == providers.PROVIDER_HOSTED:
            if provider.delete_session(session.provider_session_id):
                provider_deleted += 1
            else:
                provider_failed += 1
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
                   {"partyId": party_id, "sessions": len(sessions), "providerDeleted": provider_deleted,
                    "providerFailed": provider_failed},
                   correlation_id=correlation_id, actor_kind="admin", actor_id=str(admin.id))
    db.commit()
    return {"sessions": len(sessions), "provider_deleted": provider_deleted, "provider_failed": provider_failed}


# -- retention (Section 9.2) ----------------------------------------------------

_DECIDED_STATES = ("VERIFIED", "FAILED", "REVERIFICATION_REQUIRED", "ACTION_REQUIRED")


def purge_expired_evidence(db: Session) -> int:
    """Scheduled job: deletes raw evidence files whose country pack retention
    period after the decision has passed. The decision, masked number and
    hashes are kept; nothing is purged while undecided or under a pack
    without a retention period."""
    from app.core.identity_uploads import resolve_identity_document_path

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
            path = resolve_identity_document_path(session.document_file_path)
            if path.is_file():
                path.unlink()
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
    reviewed = [s for s in submitted if s.verifier_admin_id is not None or s.method_type == "MANUAL"
                or "ESCALATED" in (s.reason_codes or [])]

    def hours(s: IdentityVerification) -> float | None:
        start, end = _as_utc(s.submitted_at), _as_utc(s.decided_at)
        return (end - start).total_seconds() / 3600 if start and end else None

    automated_times = [h for s in submitted if s.verifier_admin_id is None and (h := hours(s)) is not None]
    manual_times = [h for s in submitted if s.verifier_admin_id is not None and (h := hours(s)) is not None]

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
    provider_errors = sum(1 for s in started if "PROVIDER_UNAVAILABLE" in (s.reason_codes or []))

    def rate(part: int, whole: int) -> float | None:
        return round(100 * part / whole, 1) if whole else None

    return {
        "period_days": days,
        "started": len(started),
        "submitted": len(submitted),
        "verified": len(verified),
        "start_to_verified_rate": rate(len(verified), len(started)),
        "manual_review_rate": rate(len(reviewed), len(submitted)),
        "action_required_recovery_rate": rate(len(recovered), len(action_parties)),
        "provider_error_rate": rate(provider_errors, len(started)),
        "time_to_decision_hours": {
            "automated": {"median": _percentile(automated_times, 50), "p90": _percentile(automated_times, 90)},
            "manual": {"median": _percentile(manual_times, 50), "p90": _percentile(manual_times, 90)},
        },
        "by_method": by_method,
        "reason_codes": dict(sorted(reason_counts.items(), key=lambda kv: -kv[1])),
        "pending_review": db.scalar(select(func.count(IdentityVerification.id)).where(
            IdentityVerification.session_state == "PENDING_REVIEW")) or 0,
        **_provider_metrics(db, since, sessions),
    }


def _provider_metrics(db: Session, since: datetime, sessions: list[IdentityVerification]) -> dict:
    """ZR-IDV-ADR-001 Section 14 -- provider funnel and webhook reliability."""
    from app.core.config import settings

    events = list(db.scalars(select(IdentityProviderEvent).where(IdentityProviderEvent.received_at >= since)))
    outcomes: dict[str, int] = {}
    for s in sessions:
        if s.provider_decision:
            outcomes[s.provider_decision] = outcomes.get(s.provider_decision, 0) + 1
    hosted = [s for s in sessions if s.provider_session_url_encrypted or s.provider_session_id]
    lags = [((_as_utc(e.processed_at) - _as_utc(e.received_at)).total_seconds())
            for e in events if e.processed_at and e.received_at]
    valid = [e for e in events if e.status != "rejected"]
    duplicates = sum(e.duplicate_count for e in valid)
    stale_cutoff = _now() - timedelta(minutes=settings.veriff_reconcile_after_minutes)
    return {
        "provider_outcomes": outcomes,
        "provider_sessions_created": sum(1 for s in hosted if s.provider_session_id),
        "webhook_auth_failures": sum(1 for e in events if e.status == "rejected"),
        "webhook_events": len(valid),
        "webhook_duplicate_rate": round(100 * duplicates / (len(valid) + duplicates), 1) if valid else None,
        "webhook_processing_lag_seconds": _percentile(lags, 50),
        "webhook_unprocessed": sum(1 for e in valid if e.status == "accepted"),
        "webhook_failed": sum(1 for e in valid if e.status == "failed"),
        "reconciled_sessions": sum(1 for s in sessions if (s.match_results or {}).get("reconciled_at")),
        "stale_sessions": sum(1 for s in hosted if s.session_state in ("IN_PROGRESS", "PROCESSING")
                              and _as_utc(s.updated_at) and _as_utc(s.updated_at) <= stale_cutoff),
    }
