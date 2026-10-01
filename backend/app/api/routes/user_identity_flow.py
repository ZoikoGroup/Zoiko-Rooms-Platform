"""ZR-IDENTITY-001 Section 8.3 -- the person's identity verification flow:
profile + details, country policy, sessions (create/reuse, document,
submit, alternative, resume/phone handoff) and the provider webhook.
Every status returned is server-authoritative."""

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.correlation import get_correlation_id
from app.core.identity_uploads import save_identity_document
from app.crud import evidence_vault as evidence_vault_crud
from app.db.session import get_db
from app.models.identity_profile import IdentityProfile
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from app.models.user_account import UserAccount
from app.schemas.identity import (
    IdentityAlternativeRequest,
    IdentityCountryRead,
    IdentityDetailsUpdate,
    IdentityHandoffClaim,
    IdentityHandoffRead,
    IdentityPackRead,
    IdentityProfileRead,
    IdentitySessionCreate,
    IdentitySessionRead,
    IdentitySubmit,
)
from app.services.identity import policy
from app.services.identity import service as identity_service
from app.services.identity.reason_codes import describe

router = APIRouter(prefix="/api/users/identity", tags=["user-identity-flow"], dependencies=[Depends(get_current_user)])
webhook_router = APIRouter(prefix="/api/webhooks/identity", tags=["identity-webhooks"])


def session_read(session: IdentityVerification, *, include_launch_url: bool = False) -> dict:
    info = describe(session.reason_codes or [])
    url = identity_service.launch_url(session)
    restartable = session.session_state == "FAILED" or (
        session.session_state in identity_service.OPEN_SESSION_STATES
        and bool(set(session.reason_codes or []) & set(identity_service._RESTARTABLE))
    )
    return {
        "launch_available": url is not None,
        "launch_url": url if include_launch_url else None,
        "can_restart": restartable and "AGE_REQUIREMENT_NOT_MET" not in (session.reason_codes or []),
        "id": session.id,
        "state": session.session_state,
        "method": session.method_type,
        "role_context": session.role_context,
        "document_type": session.document_type,
        "masked_document_number": session.masked_document_number,
        "has_document": session.has_document,
        "document_original_name": session.document_file_original_name,
        "attested": session.attested_at is not None,
        "reason_codes": [] if session.session_state == "PENDING_REVIEW" else list(session.reason_codes or []),
        "message": info["message"],
        "actions": info["actions"],
        "escalated": False,  # internal; never shown to the person
        "created_at": session.created_at,
        "submitted_at": session.submitted_at,
        "decided_at": session.decided_at,
    }


def _profile_read(db: Session, profile: IdentityProfile) -> dict:
    current = None
    open_or_latest = db.query(IdentityVerification).filter(
        IdentityVerification.party_id == profile.party_id,
    ).order_by(IdentityVerification.id.desc()).first()
    if open_or_latest is not None:
        current = session_read(open_or_latest)
    return {
        "state": profile.state,
        "assurance_level": profile.assurance_level,
        "given_name": profile.given_name,
        "middle_names": profile.middle_names,
        "family_name": profile.family_name,
        "date_of_birth": profile.date_of_birth,
        "country_code": profile.country_code,
        "verified_legal_name": profile.verified_legal_name,
        "verified_at": profile.verified_at,
        "reverification_required_at": profile.reverification_required_at,
        "verification_method": profile.verification_method,
        "dashboard": identity_service.dashboard(profile),
        "pack": policy.pack_summary(policy.get_pack(db, profile.country_code), db),
        "current_session": current,
    }


def _profile_for(db: Session, user: UserAccount) -> IdentityProfile:
    party_id = identity_service._require_party(user)
    profile = identity_service.get_or_create_profile(db, party_id)
    if not profile.country_code:
        party = db.get(Party, party_id)
        profile.country_code = policy.country_from_jurisdiction(party.jurisdiction if party else "")
    db.commit()
    return profile


@router.get("", response_model=IdentityProfileRead)
def get_my_identity(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return _profile_read(db, _profile_for(db, user))


@router.put("/details", response_model=IdentityProfileRead)
def put_my_identity_details(
    payload: IdentityDetailsUpdate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    profile = identity_service.update_details(
        db, user, given_name=payload.given_name, middle_names=payload.middle_names,
        family_name=payload.family_name, date_of_birth=payload.date_of_birth, country_code=payload.country_code,
        current_password=payload.current_password, correlation_id=get_correlation_id(request),
    )
    return _profile_read(db, profile)


@router.get("/countries", response_model=list[IdentityCountryRead])
def get_identity_countries(db: Session = Depends(get_db)):
    return [{"country_code": p.country_code, "country_name": p.country_name} for p in policy.list_countries(db)]


@router.get("/policy", response_model=IdentityPackRead)
def get_identity_policy(country: str = "", db: Session = Depends(get_db)):
    return policy.pack_summary(policy.get_pack(db, country), db)


@router.post("/verifications", response_model=IdentitySessionRead, status_code=status.HTTP_201_CREATED)
def post_identity_session(
    payload: IdentitySessionCreate, request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    _profile_for(db, user)
    session = identity_service.start_session(
        db, user, method=payload.method, role_context=payload.role_context,
        idempotency_key=(idempotency_key or "").strip()[:100] or None, correlation_id=get_correlation_id(request),
    )
    return session_read(session)


@router.get("/verifications/{session_id}", response_model=IdentitySessionRead)
def get_identity_session(session_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return session_read(identity_service.get_session_for_user(db, user, session_id))


@router.post("/verifications/{session_id}/document", response_model=IdentitySessionRead)
async def post_identity_document(
    session_id: int, request: Request,
    document_type: str = Form(...), document_number: str = Form(""), file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    session = identity_service.get_session_for_user(db, user, session_id)
    stored_filename, original_filename, content_type, file_size, sha256_hash = await save_identity_document(file)
    duplicate = evidence_vault_crud.find_duplicate_by_hash(db, sha256_hash, exclude_uploaded_by_user_id=user.id)
    duplicate_of = (
        int(duplicate.related_entity_id)
        if duplicate and duplicate.related_entity_type == "identity_verification" else None
    )
    session = identity_service.attach_document(
        db, user, session, document_type=document_type, document_number=document_number,
        stored_filename=stored_filename, original_filename=original_filename, content_type=content_type,
        file_size=file_size, duplicate_of_verification_id=duplicate_of, correlation_id=get_correlation_id(request),
    )
    evidence_vault_crud.register_evidence_artifact(
        db, related_entity_type="identity_verification", related_entity_id=str(session.id),
        stored_filename=stored_filename, sha256_hash=sha256_hash, original_filename=original_filename,
        content_type=content_type, file_size=file_size, uploaded_by_user_id=user.id,
    )
    db.commit()
    return session_read(session)


@router.post("/verifications/{session_id}/submit", response_model=IdentitySessionRead)
def post_identity_submit(
    session_id: int, payload: IdentitySubmit, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    session = identity_service.get_session_for_user(db, user, session_id)
    return session_read(identity_service.submit(
        db, user, session, attested=payload.attested, correlation_id=get_correlation_id(request),
    ), include_launch_url=True)


@router.post("/verifications/{session_id}/launch", response_model=IdentitySessionRead)
def post_identity_launch(session_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """ZR-IDV-ADR-001 step 4: the provider's capture URL for this person's own
    open session (to resume, or after a resubmission request)."""
    session = identity_service.get_session_for_user(db, user, session_id)
    if identity_service.launch_url(session) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This verification can't be continued -- start again")
    return session_read(session, include_launch_url=True)


@router.post("/verifications/{session_id}/refresh", response_model=IdentitySessionRead)
def post_identity_refresh(session_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Check with the provider whether a decision is in (rate-limited)."""
    session = identity_service.get_session_for_user(db, user, session_id)
    return session_read(identity_service.refresh_from_provider(db, user, session))


@router.post("/verifications/{session_id}/restart", response_model=IdentitySessionRead, status_code=status.HTTP_201_CREATED)
def post_identity_restart(
    session_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-IDV-ADR-001 Section 10: a new attempt after expiry / abandonment /
    a decline, within the country's daily attempt limit."""
    session = identity_service.get_session_for_user(db, user, session_id)
    return session_read(identity_service.restart(db, user, session, correlation_id=get_correlation_id(request)))


@router.post("/verifications/{session_id}/alternative", response_model=IdentitySessionRead)
def post_identity_alternative(
    session_id: int, payload: IdentityAlternativeRequest, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    session = identity_service.get_session_for_user(db, user, session_id)
    return session_read(identity_service.request_alternative(
        db, user, session, reason_code=payload.reason_code, note=payload.note,
        correlation_id=get_correlation_id(request),
    ))


@router.post("/verifications/{session_id}/resume", response_model=IdentityHandoffRead)
def post_identity_resume(session_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """A fresh short-lived token to continue this session on another device."""
    session = identity_service.get_session_for_user(db, user, session_id)
    token = identity_service.issue_handoff_token(db, session)
    return {"token": token, "expires_in_seconds": int(identity_service._HANDOFF_TTL.total_seconds())}


@router.post("/handoff/claim", response_model=IdentitySessionRead)
def post_identity_handoff_claim(
    payload: IdentityHandoffClaim, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    return session_read(identity_service.claim_handoff_token(db, user, payload.token))


@webhook_router.post("/{provider_code}")
async def post_identity_provider_webhook(provider_code: str, request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    return identity_service.process_webhook(
        db, provider_code, dict(request.headers), body, correlation_id=get_correlation_id(request),
    )


# ZR-IDV-ADR-001 Section 10: Veriff's decision and event webhooks. The raw
# request bytes are read untouched for the HMAC check.
veriff_webhook_router = APIRouter(prefix="/api/v1/webhooks/veriff", tags=["identity-webhooks"])


@veriff_webhook_router.post("/decision")
async def post_veriff_decision(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    return identity_service.ingest_webhook(
        db, "veriff", dict(request.headers), body, kind="decision", correlation_id=get_correlation_id(request),
    )


@veriff_webhook_router.post("/full-auto")
async def post_veriff_full_auto(request: Request, db: Session = Depends(get_db)):
    """Essential-plan "Full Auto" result webhook (used when VERIFF_PLAN=full_auto)."""
    body = await request.body()
    return identity_service.ingest_webhook(
        db, "veriff", dict(request.headers), body, kind="full_auto", correlation_id=get_correlation_id(request),
    )


@veriff_webhook_router.post("/events")
async def post_veriff_events(request: Request, db: Session = Depends(get_db)):
    """Progress only (started / submitted) -- never a verification decision."""
    body = await request.body()
    return identity_service.ingest_webhook(
        db, "veriff", dict(request.headers), body, kind="events", correlation_id=get_correlation_id(request),
    )
