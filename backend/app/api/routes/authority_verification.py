"""ZR-AUTHORITY-002 Section 12.2 API surface.

- /api/users/authority-verifications -- the host's authority assertion:
  start (per property), details, evidence, owner confirmations, submit,
  renew / reconsider, revoke, organizations and publication eligibility.
- /api/authority-confirmations/{token} -- public owner / landlord
  confirmation (link + one-time code, no account needed).
- /api/authority-verifications -- Trust & Safety: read-only case list and
  case view (every case is decided automatically), revoke, ownership
  change, metrics and Authority Regulatory Packs.

Writes take the version the client last read (If-Match header); a stale
version gets 409. Start and submit accept an Idempotency-Key."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud.audit import log_audit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.authority_verification import (
    RELATIONSHIP_TYPES, SCOPE_CODES, AuthorityEvidence, AuthorityRegulatoryPack, AuthorityVerification, Organization,
)
from app.models.domain_event import DomainEvent
from app.models.property import Property
from app.models.user_account import UserAccount
from app.schemas.common import CamelModel
from app.services import authority_service as svc

# -- schemas ---------------------------------------------------------------------


class StartIn(CamelModel):
    relationship_type: str
    room_scope_ids: list[int] = []


class DetailsIn(CamelModel):
    acting_capacity: str | None = None
    organization_id: int | None = None
    principal_name: str | None = None
    scope_codes: list[str] | None = None
    effective_at: str | None = None
    expires_at: str | None = None
    restrictions: str | None = None
    room_scope_ids: list[int] | None = None


class OrganizationIn(CamelModel):
    name: str
    registration_number: str = ""
    country_code: str = ""
    representative_role: str = ""


class ConfirmationRequestIn(CamelModel):
    requirement_id: str
    recipient_email: str
    recipient_name: str = ""


class SourceCheckIn(CamelModel):
    requirement_id: str
    source_code: str
    reference: str


class SubmitIn(CamelModel):
    attested: bool = False


class RenewIn(CamelModel):
    reconsideration_note: str | None = None


class RevokeIn(CamelModel):
    reason_code: str


class ConfirmationResponseIn(CamelModel):
    code: str
    decision: str
    responder_name: str = ""


class PackUpdateIn(CamelModel):
    country_name: str | None = None
    requirements: dict | None = None
    terminology: dict | None = None
    sublet_consent_required: bool | None = None
    co_owner_consent_required: bool | None = None
    parallel_identity_intake: bool | None = None
    default_validity_days: int | None = None
    expiring_soon_days: int | None = None
    evidence_retention_days: int | None = None
    listing_control: str | None = None


# -- serializers -----------------------------------------------------------------


def _version(if_match: str | None) -> int | None:
    if not if_match:
        return None
    try:
        return int(if_match.strip().strip('"').removeprefix("W/").strip('"'))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "If-Match must be the verification version")


def organization_read(org: Organization | None) -> dict | None:
    if org is None:
        return None
    return {"id": org.id, "name": org.name, "registrationNumber": org.registration_number,
            "countryCode": org.country_code, "representativeRole": org.representative_role, "status": org.status,
            "verifiedAt": org.verified_at}


def evidence_read(e: AuthorityEvidence) -> dict:
    return {"id": e.id, "requirementId": e.requirement_id, "evidenceType": e.evidence_type,
            "sourceType": e.source_type, "issuer": e.issuer, "documentReference": e.document_reference,
            "issuedAt": e.issued_at, "expiresAt": e.expires_at, "originalFilename": e.original_filename,
            "contentType": e.content_type, "fileSize": e.file_size, "processingStatus": e.processing_status,
            # Which listed document OCR / the text layer recognised (None = not read yet).
            "detectedDocument": e.detected_document or None,
            "recognised": e.type_matched if e.source_type == "UPLOAD" else True,
            "available": bool(e.stored_filename), "createdAt": e.created_at}


def confirmation_read(c) -> dict:
    return {"id": c.id, "kind": c.kind, "requirementId": c.requirement_id, "recipientName": c.recipient_name,
            "status": c.status, "expiresAt": c.expires_at, "respondedAt": c.responded_at,
            "responderName": c.responder_name, "createdAt": c.created_at}


def verification_read(db: Session, v: AuthorityVerification) -> dict:
    state = svc.effective_state(v)
    return {
        "id": v.id, "propertyId": v.property_id, "roomScopeIds": list(v.room_scope_ids or []),
        "relationshipType": v.relationship_type, "route": svc.route_for(v), "actingCapacity": v.acting_capacity,
        "organization": organization_read(v.organization), "countryCode": v.country_code,
        "packVersion": v.pack_version, "state": state, "assuranceLevel": v.assurance_level,
        "principalName": svc.principal_name(v), "scopeCodes": list(v.scope_codes or []),
        "restrictions": v.restrictions, "effectiveAt": v.effective_at, "expiresAt": v.expires_at,
        "revokedAt": v.revoked_at, "revocationReasonCode": v.revocation_reason_code,
        "reasonCodes": list(v.reason_codes or []), "reason": svc.describe(v.reason_codes or []),
        "requirements": svc.requirements(db, v),
        "matchResults": svc.host_match_results(db, v),
        "evidence": [evidence_read(e) for e in v.evidence if e.processing_status != "REPLACED"],
        "confirmations": [confirmation_read(c) for c in v.confirmations],
        "allowedActions": svc.allowed_actions(db, v), "previousId": v.previous_id,
        "supersededById": v.superseded_by_id, "isReconsideration": v.is_reconsideration,
        "submittedAt": v.submitted_at, "decidedAt": v.decided_at, "verifiedAt": v.verified_at,
        "createdAt": v.created_at, "version": v.version,
    }


def _evidence_response(v: AuthorityVerification, evidence_id: int):
    from urllib.parse import quote

    from fastapi.responses import Response

    evidence = next((e for e in v.evidence if e.id == evidence_id), None)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    data = svc.read_evidence(evidence)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The stored document is no longer available")
    return Response(content=data, media_type=evidence.content_type or "application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(evidence.original_filename or 'document')}",
                             "Cache-Control": "no-store"})


# -- host routes ------------------------------------------------------------------

router = APIRouter(prefix="/api/users", tags=["authority-verification"])


@router.get("/authority-verifications/policy")
def get_policy(country: str = "", db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    pack = svc.get_pack(db, country)
    db.commit()
    return {"countryCode": pack.country_code, "countryName": pack.country_name, "version": pack.version,
            "requirements": pack.requirements, "terminology": pack.terminology,
            "subletConsentRequired": pack.sublet_consent_required,
            "coOwnerConsentRequired": pack.co_owner_consent_required, "relationshipTypes": list(RELATIONSHIP_TYPES),
            "scopeCodes": list(SCOPE_CODES)}


@router.get("/authority-verifications")
def list_mine(db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    """Section 10.3 Verification Center: the host's current authority
    assertion per property and relationship (exception management only --
    no evidence or requirement detail)."""
    if not user.party_id:
        return []
    rows = db.scalars(select(AuthorityVerification).where(
        AuthorityVerification.party_id == user.party_id, AuthorityVerification.state != "SUPERSEDED",
    ).order_by(AuthorityVerification.id.desc()))
    seen, out = set(), []
    for v in rows:
        key = (v.property_id, v.relationship_type)
        if key in seen:
            continue
        seen.add(key)
        prop = db.get(Property, v.property_id)
        out.append({"id": v.id, "propertyId": v.property_id, "propertyLabel": prop.address or prop.city,
                    "propertyCity": prop.city, "relationshipType": v.relationship_type,
                    "state": svc.effective_state(v), "expiresAt": v.expires_at,
                    "reason": svc.describe(v.reason_codes or []), "allowedActions": svc.allowed_actions(db, v)})
    return out


@router.get("/properties/{property_id}/authority-verifications")
def list_for_property(property_id: int, db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    prop = svc.get_owned_property(db, user, property_id)
    rows = db.scalars(select(AuthorityVerification).where(
        AuthorityVerification.property_id == prop.id, AuthorityVerification.party_id == user.party_id,
    ).order_by(AuthorityVerification.id.desc()))
    return [verification_read(db, v) for v in rows]


@router.post("/properties/{property_id}/authority-verifications", status_code=status.HTTP_201_CREATED)
def post_start(property_id: int, payload: StartIn, request: Request,
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
               db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    v = svc.start(db, user, property_id, relationship_type=payload.relationship_type,
                  room_scope_ids=payload.room_scope_ids, idempotency_key=idempotency_key,
                  correlation_id=get_correlation_id(request))
    return verification_read(db, v)


@router.get("/properties/{property_id}/publication-eligibility")
def get_publication_eligibility(property_id: int, db: Session = Depends(get_db),
                                user: UserAccount = Depends(get_current_user)):
    return svc.publication_eligibility(db, svc.get_owned_property(db, user, property_id))


@router.get("/authority-verifications/{verification_id}")
def get_one(verification_id: int, db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    return verification_read(db, svc.get_owned(db, user, verification_id))


@router.put("/authority-verifications/{verification_id}/details")
def put_details(verification_id: int, payload: DetailsIn, request: Request,
                if_match: str | None = Header(default=None, alias="If-Match"),
                db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    v = svc.get_owned(db, user, verification_id)
    v = svc.set_details(db, user, v, **payload.model_dump(exclude_unset=True), expected_version=_version(if_match),
                        correlation_id=get_correlation_id(request))
    return verification_read(db, v)


@router.post("/authority-verifications/{verification_id}/evidence", status_code=status.HTTP_201_CREATED)
async def post_evidence(verification_id: int, request: Request, requirement_id: str = Form(..., alias="requirementId"),
                        issuer: str = Form(""), document_reference: str = Form("", alias="documentReference"), issued_at: str = Form("", alias="issuedAt"),
                        expires_at: str = Form("", alias="expiresAt"), replaces_id: int | None = Form(None, alias="replacesId"),
                        file: UploadFile = File(...), if_match: str | None = Header(default=None, alias="If-Match"),
                        db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    v = svc.get_owned(db, user, verification_id)
    content = await file.read()
    svc.add_evidence(db, user, v, requirement_id=requirement_id, content=content,
                     original_filename=file.filename or "document", issuer=issuer,
                     document_reference=document_reference, issued_at=issued_at or None,
                     expires_at=expires_at or None, replaces_id=replaces_id, expected_version=_version(if_match),
                     correlation_id=get_correlation_id(request))
    db.refresh(v)
    return verification_read(db, v)


@router.post("/authority-verifications/{verification_id}/source-checks", status_code=status.HTTP_201_CREATED)
def post_source_check(verification_id: int, payload: SourceCheckIn, request: Request,
                      if_match: str | None = Header(default=None, alias="If-Match"),
                      db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    """Sections 5.2 / 6.3 / 7.3: check a registry or connected source instead of uploading."""
    v = svc.get_owned(db, user, verification_id)
    svc.check_source(db, user, v, requirement_id=payload.requirement_id, source_code=payload.source_code,
                     reference=payload.reference, expected_version=_version(if_match),
                     correlation_id=get_correlation_id(request))
    db.refresh(v)
    return verification_read(db, v)


@router.delete("/authority-verifications/{verification_id}/evidence/{evidence_id}")
def delete_evidence(verification_id: int, evidence_id: int, if_match: str | None = Header(default=None, alias="If-Match"),
                    db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    v = svc.get_owned(db, user, verification_id)
    svc.remove_evidence(db, user, v, evidence_id, expected_version=_version(if_match))
    db.refresh(v)
    return verification_read(db, v)


@router.get("/authority-verifications/{verification_id}/evidence/{evidence_id}/document")
def get_own_evidence(verification_id: int, evidence_id: int, db: Session = Depends(get_db),
                     user: UserAccount = Depends(get_current_user)):
    return _evidence_response(svc.get_owned(db, user, verification_id), evidence_id)


@router.post("/authority-verifications/{verification_id}/owner-confirmations", status_code=status.HTTP_201_CREATED)
def post_confirmation(verification_id: int, payload: ConfirmationRequestIn, request: Request,
                      idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                      if_match: str | None = Header(default=None, alias="If-Match"),
                      db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    v = svc.get_owned(db, user, verification_id)
    svc.request_confirmation(db, user, v, requirement_id=payload.requirement_id,
                             recipient_email=payload.recipient_email, recipient_name=payload.recipient_name,
                             idempotency_key=idempotency_key, expected_version=_version(if_match),
                             correlation_id=get_correlation_id(request))
    db.refresh(v)
    return verification_read(db, v)


@router.post("/authority-verifications/{verification_id}/submit")
def post_submit(verification_id: int, payload: SubmitIn, request: Request,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                if_match: str | None = Header(default=None, alias="If-Match"),
                db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    v = svc.get_owned(db, user, verification_id)
    v = svc.submit(db, user, v, attested=payload.attested, idempotency_key=idempotency_key,
                   expected_version=_version(if_match), correlation_id=get_correlation_id(request))
    return verification_read(db, v)


@router.post("/authority-verifications/{verification_id}/renew", status_code=status.HTTP_201_CREATED)
def post_renew(verification_id: int, payload: RenewIn, request: Request, db: Session = Depends(get_db),
               user: UserAccount = Depends(get_current_user)):
    v = svc.get_owned(db, user, verification_id)
    return verification_read(db, svc.renew(db, user, v, reconsideration_note=payload.reconsideration_note,
                                           correlation_id=get_correlation_id(request)))


@router.post("/authority-verifications/{verification_id}/revoke")
def post_revoke(verification_id: int, request: Request, db: Session = Depends(get_db),
                user: UserAccount = Depends(get_current_user)):
    v = svc.get_owned(db, user, verification_id)
    return verification_read(db, svc.revoke(db, user, v, reason_code="REVOKED_BY_HOST",
                                            correlation_id=get_correlation_id(request)))


@router.get("/organizations")
def get_organizations(db: Session = Depends(get_db), user: UserAccount = Depends(get_current_user)):
    return [organization_read(o) for o in svc.list_organizations(db, user)]


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
def post_organization(payload: OrganizationIn, db: Session = Depends(get_db),
                      user: UserAccount = Depends(get_current_user)):
    return organization_read(svc.create_organization(db, user, **payload.model_dump()))


# -- public owner / landlord confirmation ---------------------------------------------

confirmation_router = APIRouter(prefix="/api/authority-confirmations", tags=["authority-confirmation"])


@confirmation_router.get("/{token}")
def get_confirmation(token: str, db: Session = Depends(get_db)):
    return svc.confirmation_summary(db, token)


@confirmation_router.post("/{token}")
def post_confirmation_response(token: str, payload: ConfirmationResponseIn, request: Request,
                               db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else ""
    return svc.respond_to_confirmation(db, token, code=payload.code, decision=payload.decision,
                                       responder_name=payload.responder_name, client_ip=client_ip,
                                       user_agent=request.headers.get("user-agent", ""))


# -- Trust & Safety ---------------------------------------------------------------------

admin_router = APIRouter(prefix="/api/authority-verifications", tags=["authority-verification-admin"],
                         dependencies=[Depends(require_super_admin)])


@admin_router.get("")
def list_queue(state: str = "all", db: Session = Depends(get_db)):
    query = select(AuthorityVerification).order_by(AuthorityVerification.submitted_at.is_(None),
                                                   AuthorityVerification.submitted_at, AuthorityVerification.id)
    if state != "all":
        query = query.where(AuthorityVerification.state == state.upper())
    return [{"id": v.id, "propertyId": v.property_id, "partyId": v.party_id, "state": svc.effective_state(v),
             "relationshipType": v.relationship_type, "route": svc.route_for(v), "countryCode": v.country_code,
             "reasonCodes": list(v.reason_codes or []), "isReconsideration": v.is_reconsideration,
             "submittedAt": v.submitted_at, "createdAt": v.created_at}
            for v in db.scalars(query.limit(200))]


@admin_router.get("/metrics")
def get_metrics(days: int = 30, db: Session = Depends(get_db)):
    return svc.metrics(db, days=days)


@admin_router.post("/properties/{property_id}/ownership-change")
def admin_ownership_change(property_id: int, request: Request, db: Session = Depends(get_db),
                           admin: AdminUser = Depends(require_super_admin)):
    """Section 9.2: record a property transfer / ownership-change signal."""
    count = svc.reopen_for_ownership_change(db, admin, property_id, correlation_id=get_correlation_id(request))
    log_audit_event(db, admin, "authority_verification.ownership_change", "property", str(property_id),
                    get_correlation_id(request), reason=f"reopened:{count}")
    db.commit()
    return {"propertyId": property_id, "reopened": count}


@admin_router.get("/packs")
def list_packs(db: Session = Depends(get_db)):
    svc.ensure_default_packs(db)
    db.commit()
    rows = db.scalars(select(AuthorityRegulatoryPack).where(AuthorityRegulatoryPack.active.is_(True))
                      .order_by(AuthorityRegulatoryPack.country_code))
    return [{"id": p.id, "countryCode": p.country_code, "countryName": p.country_name, "version": p.version,
             "requirements": svc.pack_requirements_view(p), "terminology": p.terminology,
             "subletConsentRequired": p.sublet_consent_required, "coOwnerConsentRequired": p.co_owner_consent_required,
             "parallelIdentityIntake": p.parallel_identity_intake, "defaultValidityDays": p.default_validity_days,
             "expiringSoonDays": p.expiring_soon_days, "evidenceRetentionDays": p.evidence_retention_days,
             "listingControl": p.listing_control} for p in rows]


@admin_router.put("/packs/{pack_id}")
def put_pack(pack_id: int, payload: PackUpdateIn, request: Request, db: Session = Depends(get_db),
             admin: AdminUser = Depends(require_super_admin)):
    pack = db.get(AuthorityRegulatoryPack, pack_id)
    if pack is None or not pack.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active pack not found")
    try:
        new = svc.update_pack(db, pack, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    log_audit_event(db, admin, "authority_regulatory_pack.update", "authority_regulatory_pack", str(new.id),
                    get_correlation_id(request), reason=f"{new.country_code} v{new.version}")
    db.commit()
    return {"id": new.id, "version": new.version}


@admin_router.get("/{verification_id}")
def admin_case(verification_id: int, db: Session = Depends(get_db)):
    v = db.get(AuthorityVerification, verification_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Authority verification not found")
    prop = db.get(Property, v.property_id)
    others = db.scalars(select(AuthorityVerification).where(
        AuthorityVerification.property_id == v.property_id, AuthorityVerification.id != v.id,
    ).order_by(AuthorityVerification.id.desc()).limit(20))
    events = db.scalars(select(DomainEvent).where(DomainEvent.resource_type == svc.RESOURCE,
                                                  DomainEvent.resource_id == str(v.id)).order_by(DomainEvent.id))
    evidence = [{**evidence_read(e), "readable": e.readable, "propertyMatched": e.property_matched,
                 "nameMatched": e.name_matched, "principalMatched": e.principal_matched,
                 "reusedElsewhere": e.reused_elsewhere, "tamperSignal": e.tamper_signal,
                 "scanStatus": e.scan_status, "textSource": e.text_source, "ocrConfidence": e.ocr_confidence,
                 "typeMatched": e.type_matched} for e in v.evidence]
    return {
        **verification_read(db, v), "evidence": evidence, "partyId": v.party_id,
        "verifiedLegalName": svc._verified_legal_name(db, v.party_id),
        "property": {"id": prop.id, "address": prop.address, "city": prop.city,
                     "postalCode": prop.postal_code, "verified": svc._property_verified(db, prop.id)},
        "matchResults": v.match_results or {}, "reconsiderationNote": v.reconsideration_note,
        "otherClaims": [{"id": o.id, "partyId": o.party_id, "relationshipType": o.relationship_type,
                         "state": svc.effective_state(o), "createdAt": o.created_at} for o in others],
        "events": [{"type": e.event_type, "previousState": e.previous_state, "newState": e.new_state,
                    "actorKind": e.actor_kind, "createdAt": e.occurred_at} for e in events],
    }


@admin_router.get("/{verification_id}/evidence/{evidence_id}/document")
def admin_evidence(verification_id: int, evidence_id: int, request: Request, db: Session = Depends(get_db),
                   admin: AdminUser = Depends(require_super_admin)):
    v = db.get(AuthorityVerification, verification_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Authority verification not found")
    log_audit_event(db, admin, "authority_evidence.view", "authority_evidence", str(evidence_id),
                    get_correlation_id(request))
    db.commit()
    return _evidence_response(v, evidence_id)


@admin_router.post("/{verification_id}/revoke")
def admin_revoke(verification_id: int, payload: RevokeIn, request: Request, db: Session = Depends(get_db),
                 admin: AdminUser = Depends(require_super_admin)):
    v = db.get(AuthorityVerification, verification_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Authority verification not found")
    v = svc.revoke(db, admin, v, reason_code=payload.reason_code, correlation_id=get_correlation_id(request))
    log_audit_event(db, admin, "authority_verification.revoke", svc.RESOURCE, str(v.id), get_correlation_id(request),
                    reason=payload.reason_code)
    db.commit()
    return verification_read(db, v)
