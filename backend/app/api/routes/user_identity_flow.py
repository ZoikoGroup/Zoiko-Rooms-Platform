"""ZR-IDENTITY-001 Section 8.3 -- the person's identity verification flow: profile + details, country policy, sessions
(create / reuse, attest, restart, resume / phone handoff) and the face match. The person uploads a photo of their
identity document and takes one selfie; the server compares the two faces (never storing the photos) and, if they
match, the identity is verified. No person at Zoiko reviews it. Every status returned is server-authoritative."""

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
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








@router.post("/verifications/{session_id}/face-match", response_model=IdentitySessionRead)
async def post_identity_face_match(
    session_id: int, request: Request,
    document_type: str = Form(...), document: UploadFile = File(...), selfie: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """THE identity check. The photo of the identity document and the selfie arrive together; the server compares
    the two faces in memory (nothing is stored) and the result is in the response: VERIFIED, ACTION_REQUIRED
    (clearer photos needed) or FAILED. Only the face match is checked."""
    session = identity_service.get_session_for_user(db, user, session_id)
    max_bytes = 10 * 1024 * 1024 + 1
    return session_read(identity_service.verify_faces(
        db, user, session, document_photo=await document.read(max_bytes), selfie=await selfie.read(max_bytes),
        document_type=document_type, correlation_id=get_correlation_id(request),
    ))


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
















# ZR-IDV-ADR-001 Section 10 internal API contract, at the paths the ADR names.
# Same handlers as the /api/users/identity/verifications routes above.
v1_router = APIRouter(prefix="/api/v1/identity-verifications", tags=["user-identity-flow"],
                      dependencies=[Depends(get_current_user)])
v1_router.add_api_route("", post_identity_session, methods=["POST"], response_model=IdentitySessionRead,
                        status_code=status.HTTP_201_CREATED)
v1_router.add_api_route("/{session_id}", get_identity_session, methods=["GET"], response_model=IdentitySessionRead)
v1_router.add_api_route("/{session_id}/restart", post_identity_restart, methods=["POST"],
                        response_model=IdentitySessionRead, status_code=status.HTTP_201_CREATED)
