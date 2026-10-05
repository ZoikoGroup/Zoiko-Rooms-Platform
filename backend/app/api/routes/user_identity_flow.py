"""ZR-IDENTITY-001 Section 8.3 / ZR-IDV-ADR-001 -- the person's identity
verification flow: profile + details, country policy, sessions (create /
reuse, attest, capture photos, complete, refresh, restart, resume / phone
handoff) and Veriff's webhooks. The document and selfie are photographed in
Zoiko's own screens and each photo is relayed straight to Veriff (never
stored); Veriff decides and no person at Zoiko reviews it. Every status
returned is server-authoritative."""

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.correlation import get_correlation_id
from app.db.session import get_db
from app.models.identity_profile import IdentityProfile
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from app.models.user_account import UserAccount
from app.schemas.identity import (
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


def session_read(session: IdentityVerification) -> dict:
    info = describe(session.reason_codes or [])
    restartable = session.session_state == "FAILED" or (
        session.session_state in identity_service.OPEN_SESSION_STATES
        and bool(set(session.reason_codes or []) & set(identity_service._RESTARTABLE))
    )
    return {
        "capture_available": identity_service.capture_open(session),
        "captured": identity_service.captured_contexts(session),
        "back_required": identity_service.back_side_required(session.document_type),
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


@router.post("/verifications/{session_id}/submit", response_model=IdentitySessionRead)
def post_identity_submit(
    session_id: int, payload: IdentitySubmit, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    session = identity_service.get_session_for_user(db, user, session_id)
    return session_read(identity_service.submit(
        db, user, session, attested=payload.attested, correlation_id=get_correlation_id(request),
    ))


@router.post("/verifications/{session_id}/capture", response_model=IdentitySessionRead)
async def post_identity_capture(
    session_id: int, request: Request,
    context: str = Form(...), document_type: str = Form(""), file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """One photo (document-front / document-back / face) taken in Zoiko's
    capture screen. It goes straight to Veriff -- never to disk or the
    database."""
    session = identity_service.get_session_for_user(db, user, session_id)
    content = await file.read(10 * 1024 * 1024 + 1)
    return session_read(identity_service.capture_photo(
        db, user, session, context=context, content=content, document_type=document_type,
        correlation_id=get_correlation_id(request),
    ))


@router.post("/verifications/{session_id}/complete", response_model=IdentitySessionRead)
def post_identity_complete(
    session_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """All photos taken: Veriff starts deciding (result by webhook / decision API)."""
    session = identity_service.get_session_for_user(db, user, session_id)
    return session_read(identity_service.complete_capture(db, user, session, correlation_id=get_correlation_id(request)))


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


def _client_ip(request: Request) -> str:
    """The webhook caller's address for the optional IP allow-list. Behind
    N trusted proxies it's the N-th address from the right of
    X-Forwarded-For (the ones those proxies appended), so a caller can't
    choose it by sending the header themselves."""
    hops = settings.veriff_webhook_trusted_proxy_hops
    if hops > 0:
        chain = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
        if len(chain) >= hops:
            return chain[-hops]
        return ""
    return request.client.host if request.client else ""


def _process_events_later(app, event_ids: list[int], correlation_id: str) -> None:
    """Runs after the acknowledgement is sent (ADR Section 8: authenticate,
    persist, acknowledge, then process). Uses its own database session; an
    event that fails here stays "accepted" for the scheduled sweeper."""
    session_factory = app.dependency_overrides.get(get_db, get_db)
    sessions = session_factory()
    db = next(sessions)
    try:
        identity_service.process_webhook_events(db, event_ids, correlation_id=correlation_id)
    finally:
        sessions.close()


async def _ingest(request: Request, background: BackgroundTasks, db: Session, provider_code: str, kind: str) -> dict:
    body = await request.body()  # the exact raw bytes, for the HMAC check
    correlation_id = get_correlation_id(request)
    result = identity_service.ingest_webhook(
        db, provider_code, dict(request.headers), body, kind=kind, client_ip=_client_ip(request),
        correlation_id=correlation_id,
    )
    event_ids = result.pop("event_ids")
    if event_ids:
        background.add_task(_process_events_later, request.app, event_ids, correlation_id)
    return result


# ZR-IDV-ADR-001 Section 10: Veriff's decision and event webhooks.
veriff_webhook_router = APIRouter(prefix="/api/v1/webhooks/veriff", tags=["identity-webhooks"])


@veriff_webhook_router.post("/decision")
async def post_veriff_decision(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    return await _ingest(request, background, db, "veriff", "decision")


@veriff_webhook_router.post("/full-auto")
async def post_veriff_full_auto(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Essential-plan "Full Auto" result webhook (used when VERIFF_PLAN=full_auto)."""
    return await _ingest(request, background, db, "veriff", "full_auto")


@veriff_webhook_router.post("/events")
async def post_veriff_events(request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Progress only (started / submitted) -- never a verification decision."""
    return await _ingest(request, background, db, "veriff", "events")


# ZR-IDV-ADR-001 Section 10 internal API contract, at the paths the ADR names.
# Same handlers as the /api/users/identity/verifications routes above.
v1_router = APIRouter(prefix="/api/v1/identity-verifications", tags=["user-identity-flow"],
                      dependencies=[Depends(get_current_user)])
v1_router.add_api_route("", post_identity_session, methods=["POST"], response_model=IdentitySessionRead,
                        status_code=status.HTTP_201_CREATED)
v1_router.add_api_route("/{session_id}", get_identity_session, methods=["GET"], response_model=IdentitySessionRead)
v1_router.add_api_route("/{session_id}/restart", post_identity_restart, methods=["POST"],
                        response_model=IdentitySessionRead, status_code=status.HTTP_201_CREATED)
