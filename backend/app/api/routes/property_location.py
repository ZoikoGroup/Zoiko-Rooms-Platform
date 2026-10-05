"""ZR-PROPERTY-VERIFY-001 Section 12.3 API surface.

- /api/users/property-verifications -- the host's verification session.
- /api/location -- the provider-neutral location API (suggestions, retrieve,
  validate-address, geocode). Server-side proxy: the provider key never
  reaches the browser; throttled per user.
- /api/property-verifications -- Trust & Safety queue, case, review, metrics
  and Property Regulatory Packs (super admin).

Writes take the session version the client last read (If-Match header);
a stale version gets 409 (Section 13.3)."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud.audit import log_audit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.domain_event import DomainEvent
from app.models.property import Property
from app.models.property_location import PropertyLocationEvidence, PropertyLocationVerification, PropertyRegulatoryPack
from app.models.user_account import UserAccount
from app.schemas.common import CamelModel
from app.services import location as loc
from app.services import property_location_service as svc

# -- schemas ---------------------------------------------------------------------


class AddressIn(CamelModel):
    address_line_1: str = ""
    address_line_2: str = ""
    subpremise: str = ""
    locality: str = ""
    administrative_area: str = ""
    postal_code: str = ""
    country_code: str = ""


class StartIn(CamelModel):
    property_id: int


class SetAddressIn(CamelModel):
    address: AddressIn
    entry_mode: str = "MANUAL"
    provider_place_id: str = ""


class ConfirmAddressIn(CamelModel):
    use_suggestion: bool = False


class ConfirmLocationIn(CamelModel):
    action: str
    latitude: float | None = None
    longitude: float | None = None
    reason: str = ""


class UnitIn(CamelModel):
    property_kind: str
    building_name: str = ""
    unit: str = ""
    floor: str = ""


class SubmitIn(CamelModel):
    attested: bool = False


class SuggestIn(CamelModel):
    query: str = Field(max_length=200)
    country: str = ""
    session_token: str = Field(default="", max_length=100)


class RetrieveIn(CamelModel):
    id: str = Field(max_length=300)
    session_token: str = Field(default="", max_length=100)


class LocationAddressIn(CamelModel):
    address: AddressIn


class ReviewIn(CamelModel):
    decision: str
    reason_code: str
    note: str = ""


class PackUpdateIn(CamelModel):
    country_name: str | None = None
    required_address_fields: list[str] | None = None
    accepted_evidence_types: list[str] | None = None
    unit_required_for: list[str] | None = None
    pin_move_review_meters: int | None = None
    public_location_decimals: int | None = None
    validity_days: int | None = None
    expiring_soon_days: int | None = None
    evidence_retention_days: int | None = None


# -- serializers -----------------------------------------------------------------

_CONFIDENCE = {"ROOFTOP": "EXACT", "PARCEL": "HIGH", "INTERPOLATED": "HIGH", "STREET": "MEDIUM", "APPROXIMATE": "LOW"}


def _addr(data: dict | None) -> dict | None:
    """Address components in the API's camelCase."""
    if not data:
        return None
    from pydantic.alias_generators import to_camel

    return {to_camel(k): v for k, v in data.items()}


def _point(lat, lng):
    return {"latitude": lat, "longitude": lng} if lat is not None and lng is not None else None


def session_read(v: PropertyLocationVerification) -> dict:
    """Host view: safe reason codes and plain-language copy only -- no
    provider scores, internal thresholds or other hosts' data. The host may
    see their own precise location (Section 10)."""
    state = svc.effective_state(v)
    info = svc.describe(v.reason_codes or [])
    return {
        "id": v.id, "propertyId": v.property_id, "state": state, "version": v.version,
        "countryCode": v.country_code, "entryMode": v.entry_mode,
        "submittedAddress": _addr(v.submitted_address) or {}, "canonicalAddress": _addr(v.canonical_address) or {},
        "suggestedAddress": _addr(v.suggested_address), "addressStatus": v.address_status,
        "addressConfirmed": v.address_confirmed_at is not None,
        "geocodeStatus": v.geocode_status, "locationConfidence": _CONFIDENCE.get(v.location_precision, ""),
        "originalLocation": _point(v.original_latitude, v.original_longitude),
        "confirmedLocation": _point(v.confirmed_latitude, v.confirmed_longitude),
        "pinStatus": v.pin_status, "pinMovedMeters": v.pin_moved_meters, "pinReverseGeocode": v.pin_reverse_geocode,
        "propertyKind": v.property_kind, "buildingName": v.building_name, "unit": v.unit, "floor": v.floor,
        "possibleDuplicate": bool(v.duplicate_of_property_id),
        "duplicateHostAnswer": v.duplicate_host_answer, "duplicateHostNote": v.duplicate_host_note,
        # What we could read from each document, as plain checks -- never OCR
        # scores or thresholds (Section 8 "do not expose scores").
        "evidence": [{"id": e.id, "evidenceType": e.evidence_type, "originalFilename": e.original_filename,
                      "contentType": e.content_type, "createdAt": e.created_at,
                      "reading": {"quality": e.quality, "addressFound": e.address_matched,
                                  "postalCodeFound": e.postal_matched, "unitFound": e.unit_matched,
                                  "looksLikeChosenType": e.document_type_matched,
                                  "outdated": "EVIDENCE_OUTDATED" in (e.signals or [])}} for e in v.evidence],
        "sourceCheck": v.source_check,
        "reasonCodes": list(v.reason_codes or []), "message": info["message"], "cta": info["cta"],
        "submittedAt": v.submitted_at, "verifiedAt": v.verified_at, "expiresAt": v.expires_at,
        "editable": v.state in svc.OPEN_STATES,
        "canRestart": state in ("REJECTED", "EXPIRED", "INVALIDATED", "EXPIRING_SOON"),
    }


def pack_read(pack: PropertyRegulatoryPack) -> dict:
    return {
        "id": pack.id, "countryCode": pack.country_code, "countryName": pack.country_name, "version": pack.version,
        "requiredAddressFields": list(pack.required_address_fields or []),
        "acceptedEvidenceTypes": list(pack.accepted_evidence_types or []),
        "unitRequiredFor": list(pack.unit_required_for or []), "pinMoveReviewMeters": pack.pin_move_review_meters,
        "publicLocationDecimals": pack.public_location_decimals, "validityDays": pack.validity_days,
        "expiringSoonDays": pack.expiring_soon_days, "evidenceRetentionDays": pack.evidence_retention_days,
    }


def _version(if_match: str | None) -> int | None:
    if not if_match:
        return None
    try:
        return int(if_match.strip().strip('"').removeprefix("W/").strip('"'))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "If-Match must be the verification version")


# -- host routes ------------------------------------------------------------------

router = APIRouter(prefix="/api/users/property-verifications", tags=["property-verification"],
                   dependencies=[Depends(get_current_user)])


@router.post("", status_code=status.HTTP_201_CREATED)
def post_start(payload: StartIn, request: Request,
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
               user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    v = svc.start(db, user, payload.property_id, idempotency_key=(idempotency_key or "").strip()[:100] or None,
                  correlation_id=get_correlation_id(request))
    return session_read(v)


@router.get("/policy")
def get_policy(country: str = "", db: Session = Depends(get_db)):
    pack = svc.get_pack(db, country)
    db.commit()
    return {**pack_read(pack), **loc.capabilities()}


@router.get("/properties/{property_id}")
def get_for_property(property_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    svc.get_owned_property(db, user, property_id)
    v = svc.latest_for_property(db, property_id)
    return {"propertyId": property_id, "state": svc.effective_state(v), "verification": session_read(v) if v else None}


@router.get("/rooms/{room_id}")
def get_for_room(room_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Which property a room belongs to, with its verification -- for screens
    that only know the room (e.g. My Listings)."""
    from app.models.room import Room

    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    prop = svc.get_owned_property(db, user, room.property_id)
    v = svc.latest_for_property(db, prop.id)
    return {"propertyId": prop.id, "propertyLabel": f"{prop.address} · Property #{prop.id}",
            "state": svc.effective_state(v), "verification": session_read(v) if v else None}


@router.get("/{verification_id}")
def get_session(verification_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return session_read(svc.get_owned_verification(db, user, verification_id))


@router.put("/{verification_id}/address")
def put_address(verification_id: int, payload: SetAddressIn, request: Request,
                if_match: str | None = Header(default=None, alias="If-Match"),
                user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    v = svc.get_owned_verification(db, user, verification_id)
    return session_read(svc.set_address(
        db, user, v, address=payload.address.model_dump(), entry_mode=payload.entry_mode.upper(),
        provider_place_id=payload.provider_place_id, expected_version=_version(if_match),
        correlation_id=get_correlation_id(request)))


@router.post("/{verification_id}/address/confirm")
def post_confirm_address(verification_id: int, payload: ConfirmAddressIn, request: Request,
                         if_match: str | None = Header(default=None, alias="If-Match"),
                         user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    v = svc.get_owned_verification(db, user, verification_id)
    return session_read(svc.confirm_address(db, user, v, use_suggestion=payload.use_suggestion,
                                            expected_version=_version(if_match), correlation_id=get_correlation_id(request)))


@router.post("/{verification_id}/confirm-location")
def post_confirm_location(verification_id: int, payload: ConfirmLocationIn, request: Request,
                          if_match: str | None = Header(default=None, alias="If-Match"),
                          user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    import hashlib

    v = svc.get_owned_verification(db, user, verification_id)
    client_ip = request.client.host if request.client else ""
    device = {"ipHash": hashlib.sha256(client_ip.encode()).hexdigest()[:16] if client_ip else "",
              "userAgent": request.headers.get("user-agent", "")[:200]}
    return session_read(svc.confirm_location(
        db, user, v, action=payload.action.lower(), latitude=payload.latitude, longitude=payload.longitude,
        reason=payload.reason, expected_version=_version(if_match), correlation_id=get_correlation_id(request),
        device=device))


class DuplicateAnswerIn(CamelModel):
    answer: str
    note: str = ""


@router.post("/{verification_id}/duplicate-answer")
def post_duplicate_answer(verification_id: int, payload: DuplicateAnswerIn, request: Request,
                          if_match: str | None = Header(default=None, alias="If-Match"),
                          user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    v = svc.get_owned_verification(db, user, verification_id)
    return session_read(svc.answer_duplicate(db, user, v, answer=payload.answer, note=payload.note,
                                             expected_version=_version(if_match),
                                             correlation_id=get_correlation_id(request)))


@router.put("/{verification_id}/unit")
def put_unit(verification_id: int, payload: UnitIn, request: Request,
             if_match: str | None = Header(default=None, alias="If-Match"),
             user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    v = svc.get_owned_verification(db, user, verification_id)
    return session_read(svc.set_unit(
        db, user, v, property_kind=payload.property_kind, building_name=payload.building_name, unit=payload.unit,
        floor=payload.floor, expected_version=_version(if_match), correlation_id=get_correlation_id(request)))


@router.post("/{verification_id}/evidence", status_code=status.HTTP_201_CREATED)
async def post_evidence(verification_id: int, request: Request, evidence_type: str = Form(...),
                        file: UploadFile = File(...), if_match: str | None = Header(default=None, alias="If-Match"),
                        user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    v = svc.get_owned_verification(db, user, verification_id)
    content = await file.read(20 * 1024 * 1024 + 1)
    svc.add_evidence(db, user, v, evidence_type=evidence_type, content=content, original_filename=file.filename or "",
                     expected_version=_version(if_match), correlation_id=get_correlation_id(request))
    db.refresh(v)
    return session_read(v)


@router.delete("/{verification_id}/evidence/{evidence_id}")
def delete_evidence(verification_id: int, evidence_id: int, if_match: str | None = Header(default=None, alias="If-Match"),
                    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    v = svc.get_owned_verification(db, user, verification_id)
    svc.remove_evidence(db, user, v, evidence_id, expected_version=_version(if_match))
    db.refresh(v)
    return session_read(v)


@router.get("/{verification_id}/evidence/{evidence_id}/document")
def get_own_evidence(verification_id: int, evidence_id: int, user: UserAccount = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    v = svc.get_owned_verification(db, user, verification_id)
    return _evidence_response(db, v, evidence_id)


@router.post("/{verification_id}/submit")
def post_submit(verification_id: int, payload: SubmitIn, request: Request,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                if_match: str | None = Header(default=None, alias="If-Match"),
                user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    v = svc.get_owned_verification(db, user, verification_id)
    return session_read(svc.submit(
        db, user, v, attested=payload.attested, idempotency_key=(idempotency_key or "").strip()[:100] or None,
        expected_version=_version(if_match), correlation_id=get_correlation_id(request)))


@router.post("/{verification_id}/restart", status_code=status.HTTP_201_CREATED)
def post_restart(verification_id: int, request: Request, user: UserAccount = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    v = svc.get_owned_verification(db, user, verification_id)
    return session_read(svc.restart(db, user, v, correlation_id=get_correlation_id(request)))


def _evidence_response(db: Session, v: PropertyLocationVerification, evidence_id: int):
    from urllib.parse import quote

    from fastapi.responses import Response

    evidence = db.get(PropertyLocationEvidence, evidence_id)
    if evidence is None or evidence.verification_id != v.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    data = svc.read_evidence(evidence)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This document is no longer stored")
    return Response(content=data, media_type=evidence.content_type or "application/octet-stream", headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(evidence.original_filename or 'document')}",
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


# -- location API -----------------------------------------------------------------

location_router = APIRouter(prefix="/api/location", tags=["location"], dependencies=[Depends(get_current_user)])

_SUGGEST_LIMIT, _SUGGEST_WINDOW = 30, 60.0
_suggest_hits: dict[int, deque] = defaultdict(deque)


def _throttle(user: UserAccount) -> None:
    """Section 12.3: suggestions are throttled per user (anti-scraping / cost)."""
    now = time.monotonic()
    hits = _suggest_hits[user.id]
    while hits and now - hits[0] > _SUGGEST_WINDOW:
        hits.popleft()
    if len(hits) >= _SUGGEST_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many address searches -- wait a moment")
    hits.append(now)


@location_router.get("/capabilities")
def get_capabilities():
    return loc.capabilities()


@location_router.post("/suggestions")
def post_suggestions(payload: SuggestIn, user: UserAccount = Depends(get_current_user)):
    _throttle(user)
    try:
        items = loc.suggest(payload.query, payload.country.upper(), payload.session_token)
    except loc.LocationUnavailable:
        return {"available": False, "suggestions": []}
    return {"available": loc.capabilities()["autocomplete"],
            "suggestions": [{"id": s.id, "text": s.text, "secondary": s.secondary} for s in items]}


@location_router.post("/retrieve")
def post_retrieve(payload: RetrieveIn, user: UserAccount = Depends(get_current_user)):
    _throttle(user)
    try:
        address, _location, provider = loc.retrieve(payload.id, payload.session_token)
    except loc.LocationUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, svc.describe(["PROVIDER_UNAVAILABLE"])["message"])
    return {"address": _addr(address.as_dict()), "provider": provider, "providerPlaceId": payload.id}


@location_router.post("/validate-address")
def post_validate(payload: LocationAddressIn):
    try:
        result = loc.validate(loc.CanonicalAddress.from_dict(payload.address.model_dump()))
    except loc.LocationUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, svc.describe(["PROVIDER_UNAVAILABLE"])["message"])
    return {"status": result.status, "canonical": _addr(result.canonical.as_dict()),
            "suggestion": _addr(result.suggestion.as_dict()) if result.suggestion else None, "provider": result.provider}


@location_router.post("/geocode")
def post_geocode(payload: LocationAddressIn):
    try:
        location, provider = loc.geocode(loc.CanonicalAddress.from_dict(payload.address.model_dump()))
    except loc.LocationUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, svc.describe(["PROVIDER_UNAVAILABLE"])["message"])
    if location is None:
        return {"geocodeStatus": "NOT_FOUND", "provider": provider}
    return {"geocodeStatus": location.geocode_status, "confidence": _CONFIDENCE.get(location.precision, ""),
            "location": _point(location.latitude, location.longitude), "provider": provider}


class AdminSearchIn(CamelModel):
    query: str = Field(max_length=300)
    country: str = ""


admin_location_router = APIRouter(prefix="/api/admin/location", tags=["location"],
                                  dependencies=[Depends(get_current_admin)])


@admin_location_router.post("/geocode")
def post_admin_geocode(payload: AdminSearchIn):
    """Admin property pin picker: free-text search through the same adapter
    (Google primary, Mapbox / HERE fallback) -- the browser never calls a
    location provider with a server credential."""
    if not payload.query.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter an address or place")
    address = loc.CanonicalAddress(address_line_1=payload.query.strip(), country_code=payload.country.upper()[:2])
    try:
        result = loc.validate(address)
    except loc.LocationUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, svc.describe(["PROVIDER_UNAVAILABLE"])["message"])
    if result.location is None:
        return {"found": False}
    return {"found": True, "location": _point(result.location.latitude, result.location.longitude),
            "formatted": result.canonical.formatted or result.canonical.one_line(), "provider": result.provider}


# -- Trust & Safety ---------------------------------------------------------------

admin_router = APIRouter(prefix="/api/property-verifications", tags=["property-verification-admin"],
                         dependencies=[Depends(require_super_admin)])


@admin_router.get("")
def list_queue(state: str = "MANUAL_REVIEW", db: Session = Depends(get_db)):
    query = select(PropertyLocationVerification).order_by(PropertyLocationVerification.submitted_at.is_(None),
                                                          PropertyLocationVerification.submitted_at,
                                                          PropertyLocationVerification.id)
    if state != "all":
        query = query.where(PropertyLocationVerification.state == state.upper())
    rows = list(db.scalars(query.limit(200)))
    return [{"id": v.id, "propertyId": v.property_id, "state": svc.effective_state(v), "countryCode": v.country_code,
             "reasonCodes": list(v.reason_codes or []), "possibleDuplicate": bool(v.duplicate_of_property_id),
             "awaitingSecondApproval": v.first_approver_admin_id is not None and v.state == "MANUAL_REVIEW",
             "submittedAt": v.submitted_at, "createdAt": v.created_at} for v in rows]


@admin_router.get("/metrics")
def get_metrics(days: int = 30, db: Session = Depends(get_db)):
    return svc.metrics(db, days=max(1, min(days, 365)))


@admin_router.get("/review-reasons")
def get_review_reasons():
    return {d: [{"code": c, "message": svc.REASONS[c][0]} for c in codes] for d, codes in svc.REVIEW_REASONS.items()}


@admin_router.get("/packs")
def list_packs(db: Session = Depends(get_db)):
    svc.ensure_default_packs(db)
    db.commit()
    return [pack_read(p) for p in db.scalars(select(PropertyRegulatoryPack).where(PropertyRegulatoryPack.active.is_(True))
                                             .order_by(PropertyRegulatoryPack.country_code))]


@admin_router.put("/packs/{pack_id}")
def put_pack(pack_id: int, payload: PackUpdateIn, request: Request, admin: AdminUser = Depends(require_super_admin),
             db: Session = Depends(get_db)):
    pack = db.get(PropertyRegulatoryPack, pack_id)
    if pack is None or not pack.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pack not found")
    try:
        new = svc.update_pack(db, pack, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    log_audit_event(db, admin, "property_regulatory_pack.update", "property_regulatory_pack", str(new.id),
                    get_correlation_id(request), reason=f"{new.country_code} v{new.version}")
    db.commit()
    return pack_read(new)


@admin_router.get("/{verification_id}/case")
def get_case(verification_id: int, db: Session = Depends(get_db)):
    """Section 16 reviewer view: what the case needs, nothing more."""
    from app.crud.identity_verification import get_verified_identity_for_party

    v = db.get(PropertyLocationVerification, verification_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property verification not found")
    prop = db.get(Property, v.property_id)
    events = db.scalars(select(DomainEvent).where(DomainEvent.resource_type == svc.RESOURCE,
                                                  DomainEvent.resource_id == str(v.id))
                        .order_by(DomainEvent.occurred_at, DomainEvent.id))
    pack = svc.get_pack(db, v.country_code)
    return {
        **session_read(v),
        "propertyLabel": f"Property #{prop.id}" if prop else "",
        "jurisdiction": prop.jurisdiction_code if prop else "",
        "hostIdentityVerified": get_verified_identity_for_party(db, v.party_id) is not None,
        "provider": v.provider, "locationPrecision": v.location_precision,
        "pinMovePolicyMeters": pack.pin_move_review_meters,
        "pinAdjustReason": v.pin_adjust_reason, "pinAdjustCount": v.pin_adjust_count,
        "duplicateOfPropertyId": v.duplicate_of_property_id,
        "duplicateHostAnswer": v.duplicate_host_answer, "duplicateHostNote": v.duplicate_host_note,
        "pinAdjustDevice": v.pin_adjust_device or {},
        "awaitingSecondApproval": v.first_approver_admin_id is not None and v.state == "MANUAL_REVIEW",
        "firstApproverAdminId": v.first_approver_admin_id,
        "existenceStatus": v.existence_status,
        "evidenceDetail": [{"id": e.id, "evidenceType": e.evidence_type, "originalFilename": e.original_filename,
                            "contentType": e.content_type, "readable": e.readable, "addressMatched": e.address_matched,
                            "unitMatched": e.unit_matched, "reusedElsewhere": e.reused_elsewhere,
                            "scanStatus": e.scan_status, "purged": e.purged_at is not None,
                            "textSource": e.text_source, "ocrConfidence": e.ocr_confidence, "quality": e.quality,
                            "postalMatched": e.postal_matched, "ownerNameMatched": e.owner_name_matched,
                            "documentTypeMatched": e.document_type_matched, "documentYear": e.document_year,
                            "referenceNumber": e.reference_number, "signals": list(e.signals or [])}
                           for e in v.evidence],
        "history": [{"eventType": e.event_type, "occurredAt": e.occurred_at, "actorKind": e.actor_kind,
                     "previousState": e.previous_state, "newState": e.new_state,
                     "reasonCodes": (e.payload or {}).get("reasonCodes", [])} for e in events],
    }


@admin_router.post("/{verification_id}/review")
def post_review(verification_id: int, payload: ReviewIn, request: Request,
                admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    v = db.get(PropertyLocationVerification, verification_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property verification not found")
    updated = svc.review(db, admin, v, decision=payload.decision, reason_code=payload.reason_code, note=payload.note,
                         correlation_id=get_correlation_id(request))
    log_audit_event(db, admin, f"property_location_verification.review.{payload.decision.lower()}",
                    svc.RESOURCE, str(v.id), get_correlation_id(request), reason=payload.reason_code)
    db.commit()
    return session_read(updated)


@admin_router.get("/{verification_id}/evidence/{evidence_id}/document")
def get_admin_evidence(verification_id: int, evidence_id: int, request: Request,
                       admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db)):
    v = db.get(PropertyLocationVerification, verification_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property verification not found")
    log_audit_event(db, admin, "property_location_verification.evidence_viewed", svc.RESOURCE, str(v.id),
                    get_correlation_id(request), reason=f"evidence:{evidence_id}")
    db.commit()
    return _evidence_response(db, v, evidence_id)
