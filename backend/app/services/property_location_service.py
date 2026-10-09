"""ZR-PROPERTY-VERIFY-001 orchestration -- the property & location
verification flow (Section 5), state model (Section 7), confidence engine
(Section 8), evidence policy (Section 9), events (Section 13) and fraud
controls (Section 14).

Flow: start -> set_address (validate) -> confirm_address -> confirm_location
(controlled pin) -> set_unit -> add_evidence -> submit (attest; automated
decision) -> VERIFIED / MANUAL_REVIEW / ACTION_REQUIRED -> review (Trust &
Safety) where needed.

Hard rules:
- address / geocode / pin success never produce VERIFIED on their own: an
  existence signal (matching evidence of an accepted type, a source check, or
  a reviewer) is required (P0 #2, Section 7.1 EXISTENCE).
- a provider outage never approves anything (P0 #13).
- an adjusted pin never overwrites the provider's original result; large or
  cross-address moves become REVIEW_REQUIRED (Section 6.4).
- users see safe reason codes, never scores or provider internals (Section 8).
- events carry ids, states and reason codes -- no coordinates, documents or
  provider payloads (Section 13.2)."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.core.config import settings
from app.crud.events import emit_event
from app.models.admin_user import AdminUser
from app.models.property import Property
from app.models.property_location import (
    EVIDENCE_TYPES, PROPERTY_KINDS, REVIEW_DECISIONS, SUPPLEMENTARY_EVIDENCE_TYPES,
    PropertyLocationEvidence, PropertyLocationVerification, PropertyRegulatoryPack,
)
from app.models.user_account import UserAccount
from app.services import location as loc

logger = logging.getLogger("uvicorn.error")

OPEN_STATES = ("IN_PROGRESS", "ACTION_REQUIRED")
RESOURCE = "property_location_verification"

# Property.jurisdiction_code -> ISO country the address must be in.
JURISDICTION_COUNTRY = {"England": "GB", "GB-ENG": "GB", "GB-WLS": "GB", "GB-SCT": "GB", "GB-NIR": "GB",
                        "IN": "IN", "US": "US"}

# -- safe reason codes (Section 8 / 15 copy) -------------------------------------

REASONS: dict[str, tuple[str, str]] = {
    # code: (user-facing message, CTA)
    "NEARBY_PROPERTY_CONFLICT": ("Another property is registered at almost the same spot. We need to review the property information. You can leave this page; we'll update the status here.", "Go to dashboard"),
    "DUPLICATE_REVIEW": ("This property looks like one already on Zoiko Rooms. We need to review it. You can leave this page; we'll update the status here.", "Go to dashboard"),
    "EVIDENCE_REVIEW": ("We need to review your property evidence. You can leave this page; we'll update the status here.", "Go to dashboard"),
    "PREVIOUSLY_REJECTED": ("We need to review this property information. You can leave this page; we'll update the status here.", "Go to dashboard"),
    "POSTAL_CODE_INVALID": ("The postal code doesn't look right for this country. Check the address.", "Review address"),
    "SAME_PROPERTY_DECLARED": ("You said this is the same property as an existing listing, so it can't be verified again as a new property.", "Contact support"),
    "ADDRESS_NOT_FOUND": ("We couldn't find this address automatically. Enter it manually and we'll verify it another way.", "Enter manually"),
    "ADDRESS_PARTIAL": ("We could only partly confirm this address. We'll check the property information.", "View status"),
    "GEOCODE_AMBIGUOUS": ("We found more than one possible location. Choose the correct property.", "Choose address"),
    "HOUSE_NUMBER_NOT_ON_MAP": ("The map knows your street and area, but not your house number -- the marker is on a nearby building. Choose Adjust marker and place it on your property (you can use your phone's location, a DIGIPIN or a Plus Code).", "Adjust marker"),
    "LOW_LOCATION_CONFIDENCE": ("We found the area, but not the exact property. We'll confirm it with your evidence.", "View status"),
    "PIN_MOVED_TOO_FAR": ("The marker is too far from the address. Move it back onto your property (small corrections are fine).", "Fix location"),
    "UNIT_MISSING": ("Add the apartment, flat, or unit number so we can identify the correct property.", "Add unit"),
    "UNIT_NOT_CONFIRMED": ("We could not confirm the unit number from the evidence provided.", "Add evidence"),
    "EVIDENCE_MISMATCH": ("The property details in this document do not clearly match the address. Upload another document or review the address.", "Fix issue"),
    "EVIDENCE_POOR_QUALITY": ("We couldn't read your document clearly. Upload a clearer photo or the PDF from the official website.", "Upload clearer copy"),
    "EVIDENCE_OUTDATED": ("This document is too old to confirm the property today. Upload a recent one (from the last 3 years).", "Add evidence"),
    "EVIDENCE_TYPE_UNCLEAR": ("This document doesn't look like the type you chose. Choose the right type or upload a property tax bill, registry or title record.", "Replace document"),
    "EVIDENCE_UNREADABLE": ("We couldn't read your document. Upload the PDF from the official website or a clear photo of the full document.", "Upload document"),
    "SUPPLEMENTARY_EVIDENCE_ONLY": ("A utility bill alone can't confirm the property. Add a property tax bill, registry record, title deed or building record.", "Add evidence"),
    "EVIDENCE_REUSED": ("We need to review your property evidence. You can leave this page; we'll update the status here.", "Go to dashboard"),
    "POSSIBLE_DUPLICATE": ("This property looks like one already on Zoiko Rooms. Answer the duplicate question on the property details step.", "Answer question"),
    "PROVIDER_UNAVAILABLE": ("Location search is temporarily unavailable. Your progress is saved. Try again or enter the address manually.", "Try again"),
    "SECOND_APPROVAL_REQUIRED": ("We need to review this property information. You can leave this page; we'll update the status here.", "Go to dashboard"),
    "ADDRESS_CHANGED": ("The property address changed, so the property needs to be verified again.", "Reverify property"),
    "VERIFICATION_EXPIRED": ("This property's verification has expired. Renew it to keep listing.", "Renew verification"),
    "ADDRESS_COMPONENTS_CONFLICT": ("The city, region and postal code don't match each other. Check the address or use the suggested correction.", "Review address"),
    # reviewer codes (Section 16)
    "REVIEW_ADDRESS_MATCHES": ("Address and property checks are complete.", ""),
    "REVIEW_EVIDENCE_CORROBORATED": ("Address and property checks are complete.", ""),
    "REVIEW_MORE_EVIDENCE": ("We need more evidence to confirm this property. Upload another accepted document.", "Add evidence"),
    "REVIEW_UNIT_UNCONFIRMED": ("We could not confirm the unit number from the evidence provided.", "Add evidence"),
    "REVIEW_ADDRESS_INCORRECT": ("The address doesn't match the property evidence. Review the address.", "Review address"),
    "REVIEW_CANNOT_CONFIRM": ("We could not confirm that this property exists at the address given.", "Review reason"),
    "REVIEW_DUPLICATE_PROPERTY": ("This property is already registered on Zoiko Rooms.", "Contact support"),
    "REVIEW_FRAUD_SUSPECTED": ("We could not verify this property.", "Contact support"),
}
REVIEW_REASONS = {
    "APPROVE": ("REVIEW_ADDRESS_MATCHES", "REVIEW_EVIDENCE_CORROBORATED"),
    "REQUEST_INFO": ("REVIEW_MORE_EVIDENCE", "REVIEW_UNIT_UNCONFIRMED", "REVIEW_ADDRESS_INCORRECT"),
    "REJECT": ("REVIEW_CANNOT_CONFIRM", "REVIEW_DUPLICATE_PROPERTY", "REVIEW_FRAUD_SUSPECTED"),
}


def describe(codes: list[str]) -> dict:
    for code in codes or []:
        if code in REASONS:
            message, cta = REASONS[code]
            return {"message": message, "cta": cta}
    return {"message": "", "cta": ""}


# -- Property Regulatory Packs (Section 9) ---------------------------------------

# Section 17: field order and local field names per country.
_DEFAULT_ORDER = ["address_line_1", "address_line_2", "locality", "administrative_area", "postal_code"]
_LABELS = {
    "*": {"address_line_1": "Address line 1", "address_line_2": "Address line 2", "locality": "City / locality",
          "administrative_area": "Region / state", "postal_code": "Postal code"},
    "GB": {"address_line_1": "Address line 1", "address_line_2": "Address line 2", "locality": "Town / city",
           "administrative_area": "County (optional)", "postal_code": "Postcode"},
    "IN": {"address_line_1": "House / flat no., street", "address_line_2": "Area / locality / village",
           "locality": "City / town", "administrative_area": "State", "postal_code": "PIN code"},
    "US": {"address_line_1": "Street address", "address_line_2": "Apt, suite, unit (optional)", "locality": "City",
           "administrative_area": "State", "postal_code": "ZIP code"},
}
# Evidence files are deleted this long after the decision (Section 13 retention).
_RETENTION_DAYS = 730

_BASE_EVIDENCE = ["LAND_REGISTRY_RECORD", "PROPERTY_TAX_RECORD", "BUILDING_UNIT_RECORD", "TITLE_DEED",
                  "MORTGAGE_INSURANCE_STATEMENT", "UTILITY_BILL"]
_SEED_PACKS = (
    {"country_code": "*", "country_name": "Other countries",
     "required_address_fields": ["address_line_1", "locality", "country_code"],
     "accepted_evidence_types": _BASE_EVIDENCE, "unit_required_for": ["APARTMENT"], "pin_move_review_meters": 75,
     "address_field_order": _DEFAULT_ORDER, "address_labels": _LABELS["*"], "evidence_retention_days": _RETENTION_DAYS},
    {"country_code": "GB", "country_name": "United Kingdom",
     "required_address_fields": ["address_line_1", "locality", "postal_code", "country_code"],
     "accepted_evidence_types": ["LAND_REGISTRY_RECORD", "PROPERTY_TAX_RECORD", "TITLE_DEED", "BUILDING_UNIT_RECORD",
                                 "MORTGAGE_INSURANCE_STATEMENT", "UTILITY_BILL"],
     "unit_required_for": ["APARTMENT"], "pin_move_review_meters": 50,
     "address_field_order": _DEFAULT_ORDER, "address_labels": _LABELS["GB"], "evidence_retention_days": _RETENTION_DAYS},
    {"country_code": "IN", "country_name": "India",
     "required_address_fields": ["address_line_1", "locality", "administrative_area", "postal_code", "country_code"],
     "accepted_evidence_types": ["LAND_REGISTRY_RECORD", "PROPERTY_TAX_RECORD", "TITLE_DEED", "BUILDING_UNIT_RECORD",
                                 "MORTGAGE_INSURANCE_STATEMENT", "UTILITY_BILL"],
     "unit_required_for": ["APARTMENT"], "pin_move_review_meters": 100,
     "address_field_order": _DEFAULT_ORDER, "address_labels": _LABELS["IN"], "evidence_retention_days": _RETENTION_DAYS},
    {"country_code": "US", "country_name": "United States",
     "required_address_fields": ["address_line_1", "locality", "administrative_area", "postal_code", "country_code"],
     "accepted_evidence_types": ["PROPERTY_TAX_RECORD", "TITLE_DEED", "BUILDING_UNIT_RECORD",
                                 "MORTGAGE_INSURANCE_STATEMENT", "UTILITY_BILL"],
     "unit_required_for": ["APARTMENT"], "pin_move_review_meters": 50,
     "address_field_order": _DEFAULT_ORDER, "address_labels": _LABELS["US"], "evidence_retention_days": _RETENTION_DAYS},
)


def ensure_default_packs(db: Session) -> None:
    existing = set(db.scalars(select(PropertyRegulatoryPack.country_code)))
    for seed in _SEED_PACKS:
        if seed["country_code"] not in existing:
            db.add(PropertyRegulatoryPack(version=1, active=True, **seed))
    db.flush()


def get_pack(db: Session, country_code: str) -> PropertyRegulatoryPack:
    ensure_default_packs(db)
    code = (country_code or "").upper()
    pack = db.scalar(select(PropertyRegulatoryPack).where(
        PropertyRegulatoryPack.country_code == code, PropertyRegulatoryPack.active.is_(True)))
    return pack or db.scalar(select(PropertyRegulatoryPack).where(
        PropertyRegulatoryPack.country_code == "*", PropertyRegulatoryPack.active.is_(True)))


def update_pack(db: Session, pack: PropertyRegulatoryPack, changes: dict) -> PropertyRegulatoryPack:
    editable = ("country_name", "required_address_fields", "accepted_evidence_types", "unit_required_for",
                "pin_move_review_meters", "public_location_decimals", "validity_days", "expiring_soon_days",
                "evidence_retention_days", "address_field_order", "address_labels")
    unknown = set(changes) - set(editable)
    if unknown:
        raise ValueError(f"Not editable: {sorted(unknown)}")
    if "accepted_evidence_types" in changes and not set(changes["accepted_evidence_types"]) <= set(EVIDENCE_TYPES):
        raise ValueError("Unknown evidence type")
    if "unit_required_for" in changes and not set(changes["unit_required_for"]) <= set(PROPERTY_KINDS):
        raise ValueError("Unknown property kind")
    if "public_location_decimals" in changes and not 0 <= int(changes["public_location_decimals"]) <= 3:
        raise ValueError("public_location_decimals must be 0-3 (3 is ~110 m)")
    if "address_field_order" in changes and sorted(changes["address_field_order"]) != sorted(_DEFAULT_ORDER):
        raise ValueError(f"address_field_order must list exactly {', '.join(_DEFAULT_ORDER)}")
    if "address_labels" in changes and (set(changes["address_labels"]) - set(_DEFAULT_ORDER)
                                        or not all(isinstance(x, str) and 0 < len(x) <= 60
                                                   for x in changes["address_labels"].values())):
        raise ValueError("address_labels: names (up to 60 characters) for the address fields only")
    data = {f: getattr(pack, f) for f in editable}
    data.update(changes)
    pack.active = False
    new = PropertyRegulatoryPack(country_code=pack.country_code, version=pack.version + 1, active=True, **data)
    db.add(new)
    db.flush()
    return new


def country_for_property(prop: Property) -> str:
    return prop.country_code or loc.country_for_jurisdiction(prop.jurisdiction_code)


# -- helpers ----------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _norm(value: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (value or "").lower()).split())


def fingerprint(address: dict, unit: str) -> str:
    """Canonical address + unit fingerprint (Section 14 duplicate control).
    Same building, different units -> different fingerprints."""
    parts = [address.get("country_code", ""), (address.get("postal_code") or "").replace(" ", ""),
             address.get("address_line_1", ""), address.get("locality", ""), unit or ""]
    return hashlib.sha256("|".join(_norm(p) for p in parts).encode("utf-8")).hexdigest()


def _event(db: Session, event_type: str, v: PropertyLocationVerification, *, reason_codes: list[str] | None = None,
           previous_state: str | None = None, new_state: str | None = None, actor: UserAccount | AdminUser | None = None,
           correlation_id: str = "", extra: dict | None = None) -> None:
    actor_kind = "admin" if isinstance(actor, AdminUser) else "user" if actor else "system"
    emit_event(
        db, event_type, RESOURCE, str(v.id),
        {"propertyId": v.property_id, "verificationId": v.id, "partyId": v.party_id,
         "reasonCodes": list(reason_codes or []), **(extra or {})},
        correlation_id=correlation_id, actor_kind=actor_kind, actor_id=str(actor.id) if actor else "",
        previous_state=previous_state, new_state=new_state,
    )


def _touch(v: PropertyLocationVerification) -> None:
    v.version += 1
    v.updated_at = _now()


def _check_version(v: PropertyLocationVerification, expected: int | None) -> None:
    """Section 13.3: a stale client never silently overwrites newer data --
    every write must say which version it read."""
    if expected is None:
        raise HTTPException(status.HTTP_428_PRECONDITION_REQUIRED,
                            "Reload this verification and try again (If-Match is required)")
    if expected != v.version:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This verification changed in another window. Reload to see the latest version.")


def _editable(v: PropertyLocationVerification) -> None:
    if v.state not in OPEN_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "This verification can't be changed now")
    if v.state == "ACTION_REQUIRED":
        v.state = "IN_PROGRESS"


def effective_state(v: PropertyLocationVerification | None, now: datetime | None = None) -> str:
    if v is None:
        return "NOT_STARTED"
    now = now or _now()
    if v.state == "VERIFIED" and v.expires_at:
        expires = _utc(v.expires_at)
        if expires <= now:
            return "EXPIRED"
        if expires - now <= timedelta(days=_expiring_soon_days(object_session(v), v.country_code)):
            return "EXPIRING_SOON"
    return v.state


def _expiring_soon_days(db: Session | None, country_code: str) -> int:
    """The pack's renewal threshold, read without seeding (runs on every status read)."""
    if db is None:
        return 30
    for code in ((country_code or "").upper(), "*"):
        days = db.scalar(select(PropertyRegulatoryPack.expiring_soon_days).where(
            PropertyRegulatoryPack.country_code == code, PropertyRegulatoryPack.active.is_(True)))
        if days is not None:
            return days
    return 30


def get_owned_property(db: Session, user: UserAccount, property_id: int) -> Property:
    prop = db.get(Property, property_id)
    if prop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property not found")
    if not user.party_id or prop.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only verify your own properties")
    return prop


def get_owned_verification(db: Session, user: UserAccount, verification_id: int) -> PropertyLocationVerification:
    v = db.get(PropertyLocationVerification, verification_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property verification not found")
    if not user.party_id or v.party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own property verifications")
    return v


def latest_for_property(db: Session, property_id: int) -> PropertyLocationVerification | None:
    return db.scalar(select(PropertyLocationVerification).where(PropertyLocationVerification.property_id == property_id)
                     .order_by(PropertyLocationVerification.id.desc()))


def valid_for_property(db: Session, property_id: int) -> PropertyLocationVerification | None:
    """The publish gate (Section 2 invariant / P0 #8)."""
    now = _now()
    return db.scalar(select(PropertyLocationVerification).where(
        PropertyLocationVerification.property_id == property_id,
        PropertyLocationVerification.state == "VERIFIED",
        (PropertyLocationVerification.expires_at.is_(None)) | (PropertyLocationVerification.expires_at > now),
    ).order_by(PropertyLocationVerification.id.desc()))


# -- Step 0: start ---------------------------------------------------------------

def start(db: Session, user: UserAccount, property_id: int, *, idempotency_key: str | None = None,
          correlation_id: str = "") -> PropertyLocationVerification:
    prop = get_owned_property(db, user, property_id)
    if idempotency_key:
        existing = db.scalar(select(PropertyLocationVerification).where(
            PropertyLocationVerification.party_id == user.party_id,
            PropertyLocationVerification.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    open_session = db.scalar(select(PropertyLocationVerification).where(
        PropertyLocationVerification.property_id == prop.id,
        PropertyLocationVerification.state.in_(OPEN_STATES + ("MANUAL_REVIEW",)),
    ).order_by(PropertyLocationVerification.id.desc()))
    if open_session is not None:
        return open_session
    country = country_for_property(prop)
    pack = get_pack(db, country)
    prefill = {
        "address_line_1": prop.address_line_1 or prop.address, "address_line_2": prop.address_line_2,
        "subpremise": prop.subpremise, "locality": prop.locality or prop.city,
        "administrative_area": prop.administrative_area, "postal_code": prop.postal_code, "country_code": country,
    }
    v = PropertyLocationVerification(
        property_id=prop.id, party_id=user.party_id, state="IN_PROGRESS", country_code=country,
        pack_version=pack.version if pack else None, submitted_address=prefill, idempotency_key=idempotency_key,
        property_kind=prop.property_kind, building_name=prop.building_name, unit=prop.subpremise, floor=prop.floor,
    )
    db.add(v)
    db.flush()
    _event(db, "PROPERTY_VERIFICATION_STARTED", v, previous_state="NOT_STARTED", new_state="IN_PROGRESS",
           actor=user, correlation_id=correlation_id)
    db.commit()
    db.refresh(v)
    return v


# -- Steps 1-2: address ----------------------------------------------------------

_ADDRESS_FIELDS = ("address_line_1", "address_line_2", "subpremise", "locality", "administrative_area",
                   "postal_code", "country_code")


def _clean_address(raw: dict) -> dict:
    out = {k: str(raw.get(k) or "").strip()[:300] for k in _ADDRESS_FIELDS}
    out["country_code"] = out["country_code"].upper()[:2]
    return out


def set_address(db: Session, user: UserAccount, v: PropertyLocationVerification, *, address: dict, entry_mode: str,
                provider_place_id: str = "", expected_version: int | None = None,
                correlation_id: str = "") -> PropertyLocationVerification:
    """Screen 1 -> 2: validate & normalize the address the host selected or
    typed. Returns the standardized proposal (and any suggested correction);
    nothing is final until confirm_address."""
    _check_version(v, expected_version)
    _editable(v)
    if entry_mode not in ("SELECTED", "MANUAL"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "entryMode must be SELECTED or MANUAL")
    prop = db.get(Property, v.property_id)
    submitted = _clean_address(address)
    expected_country = loc.country_for_jurisdiction(prop.jurisdiction_code)
    if expected_country and submitted["country_code"] and submitted["country_code"] != expected_country:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"This property is listed in {loc.COUNTRY_NAMES.get(expected_country, expected_country)}, "
                            f"so the address must be there too.")
    submitted["country_code"] = submitted["country_code"] or expected_country
    pack = get_pack(db, submitted["country_code"])
    missing = [f for f in (pack.required_address_fields or []) if not submitted.get(f)]
    if missing:
        labels = {"address_line_1": "address line 1", "locality": "city / locality", "administrative_area": "region / state",
                  "postal_code": "postal code", "country_code": "country"}
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Add the " + ", ".join(labels.get(f, f) for f in missing) + " to continue.")

    v.entry_mode = entry_mode
    v.submitted_address = submitted
    v.country_code = submitted["country_code"]
    v.pack_version = pack.version
    v.provider_place_id = provider_place_id[:300] if entry_mode == "SELECTED" else ""
    v.address_confirmed_at = None
    v.pin_status = ""
    v.confirmed_latitude = v.confirmed_longitude = None
    v.reason_codes = [c for c in (v.reason_codes or []) if c not in _LOCATION_STEP_CODES]
    _event(db, "ADDRESS_SELECTED" if entry_mode == "SELECTED" else "ADDRESS_MANUALLY_ENTERED", v, actor=user,
           correlation_id=correlation_id)

    try:
        result = loc.validate(loc.CanonicalAddress.from_dict(submitted))
    except loc.LocationUnavailable:
        # Section 15 provider outage: progress saved, manual path continues.
        v.address_status = "UNRESOLVED"
        v.canonical_address = submitted
        v.suggested_address = {}
        v.geocode_status, v.location_precision = "NOT_FOUND", ""
        v.original_latitude = v.original_longitude = None
        v.reason_codes = list(dict.fromkeys([*(v.reason_codes or []), "PROVIDER_UNAVAILABLE"]))
        _touch(v)
        db.commit()
        db.refresh(v)
        return v

    canonical = result.canonical.as_dict()
    canonical["subpremise"] = canonical.get("subpremise") or submitted["subpremise"]
    if canonical.get("country_code") and expected_country and canonical["country_code"] != expected_country:
        # Section 20 "wrong city/country combination": the provider placed it elsewhere.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "This address appears to be outside the property's country. Check the city and country.")
    v.address_status = result.status
    v.canonical_address = canonical
    v.suggested_address = result.suggestion.as_dict() if result.suggestion else {}
    v.provider = result.provider
    v.provider_checked_at = _now()
    step_codes = []
    if result.status == "PARTIAL":
        step_codes.append("ADDRESS_PARTIAL")
    if result.suggestion and _components_conflict(canonical, v.suggested_address):
        # Section 20 "wrong city/country combination": the region / postal
        # code contradict each other -- the host must correct it.
        step_codes.append("ADDRESS_COMPONENTS_CONFLICT")
    v.reason_codes = list(dict.fromkeys([*(v.reason_codes or []), *step_codes]))
    _event(db, "ADDRESS_VALIDATED", v, actor=user, correlation_id=correlation_id,
           extra={"addressStatus": result.status, "provider": result.provider, "correctionSuggested": bool(result.suggestion)})
    _apply_geocode(db, v, result.location, user=user, correlation_id=correlation_id)
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


# Reason codes that describe the current address / location step; they're
# recomputed whenever the address is entered again.
_LOCATION_STEP_CODES = ("ADDRESS_NOT_FOUND", "PROVIDER_UNAVAILABLE", "ADDRESS_PARTIAL", "GEOCODE_AMBIGUOUS",
                        "LOW_LOCATION_CONFIDENCE", "ADDRESS_COMPONENTS_CONFLICT", "HOUSE_NUMBER_NOT_ON_MAP")
# When the map has no house-level point, the host places the marker on the
# property, and the map's point is only a starting place: a street-level point
# can be ~1.5 km off; an area-level one (a neighbourhood / village centre) can
# be several km off -- e.g. "Kalimandir, Bandlaguda Jagir" sits ~2 km from a
# house in that colony. Within these, the same-postal-area check and the
# document decide; beyond them the move needs review.
STREET_PIN_MOVE_METERS = 1500
APPROXIMATE_PIN_MOVE_METERS = 10_000
# A nudge this small (e.g. to the front door) never counts as "another address".
SMALL_PIN_MOVE_METERS = 25


def _components_conflict(entered: dict, suggested: dict) -> bool:
    """The provider replaced the region or postal code -- not just a spelling
    fix -- so what was entered contradicts itself."""
    for field in ("administrative_area", "postal_code"):
        a, b = _norm(entered.get(field)), _norm(suggested.get(field))
        if a and b and a.replace(" ", "") != b.replace(" ", ""):
            return True
    return False


def _apply_geocode(db: Session, v: PropertyLocationVerification, location: loc.CanonicalLocation | None, *,
                   user: UserAccount, correlation_id: str) -> None:
    if location is None:
        try:
            location, provider = loc.geocode(loc.CanonicalAddress.from_dict(v.canonical_address))
            v.provider = v.provider or provider
        except loc.LocationUnavailable:
            location = None
            v.reason_codes = list(dict.fromkeys([*(v.reason_codes or []), "PROVIDER_UNAVAILABLE"]))
    if location is None:
        v.geocode_status, v.location_precision = "NOT_FOUND", ""
        v.original_latitude = v.original_longitude = None
        if "PROVIDER_UNAVAILABLE" not in (v.reason_codes or []):
            v.reason_codes = list(dict.fromkeys([*(v.reason_codes or []), "ADDRESS_NOT_FOUND"]))
        _event(db, "GEOCODE_NOT_FOUND", v, actor=user, correlation_id=correlation_id, reason_codes=["NOT_FOUND"])
        return
    v.geocode_status = location.geocode_status
    v.location_precision = location.precision
    v.original_latitude, v.original_longitude = location.latitude, location.longitude
    if location.place_id and not v.provider_place_id:
        v.provider_place_id = location.place_id[:300]
    codes = [c for c in (v.reason_codes or [])
             if c not in ("GEOCODE_AMBIGUOUS", "LOW_LOCATION_CONFIDENCE", "HOUSE_NUMBER_NOT_ON_MAP")]
    if location.geocode_status == "AMBIGUOUS":
        codes.append("GEOCODE_AMBIGUOUS")
    elif location.house_number_matched is False:
        # The provider snapped to a nearby building (Indian door numbers are
        # often missing from map data) -- the host must place the marker.
        codes.append("HOUSE_NUMBER_NOT_ON_MAP")
    elif location.precision not in loc.AUTO_ACCEPT_PRECISIONS:
        codes.append("LOW_LOCATION_CONFIDENCE")
    v.reason_codes = codes
    _event(db, "GEOCODE_RESOLVED" if location.geocode_status == "RESOLVED" else "GEOCODE_AMBIGUOUS", v, actor=user,
           correlation_id=correlation_id, extra={"precision": location.precision})


def confirm_address(db: Session, user: UserAccount, v: PropertyLocationVerification, *, use_suggestion: bool = False,
                    expected_version: int | None = None, correlation_id: str = "") -> PropertyLocationVerification:
    """Screen 2: the host confirms the standardized address (optionally
    accepting the provider's correction)."""
    _check_version(v, expected_version)
    _editable(v)
    if not v.canonical_address:
        raise HTTPException(status.HTTP_409_CONFLICT, "Find the property address first")
    offered = bool(v.suggested_address)
    if use_suggestion:
        if not v.suggested_address:
            raise HTTPException(status.HTTP_409_CONFLICT, "There is no suggested correction to accept")
        v.canonical_address = {**v.suggested_address, "subpremise": v.canonical_address.get("subpremise", "")}
        v.suggested_address = {}
        if v.address_status == "PARTIAL":
            v.address_status = "VALIDATED"
        v.reason_codes = [c for c in (v.reason_codes or []) if c not in ("ADDRESS_COMPONENTS_CONFLICT", "ADDRESS_PARTIAL")]
        _apply_geocode(db, v, None, user=user, correlation_id=correlation_id)
    v.address_confirmed_at = _now()
    # Section 18 "address-validation correction acceptance rate".
    _event(db, "ADDRESS_CONFIRMED", v, actor=user, correlation_id=correlation_id,
           extra={"correctionOffered": offered, "correctionAccepted": bool(use_suggestion)})
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


# -- Step 3: location ------------------------------------------------------------

def confirm_location(db: Session, user: UserAccount, v: PropertyLocationVerification, *, action: str,
                     latitude: float | None = None, longitude: float | None = None, reason: str = "",
                     expected_version: int | None = None, correlation_id: str = "",
                     device: dict | None = None) -> PropertyLocationVerification:
    """Screen 3: "This is correct" (confirm) or a controlled "Adjust marker"
    (adjust). The original provider coordinates are kept; the movement,
    reverse-geocoded result and reason are recorded, and anything beyond the
    pack threshold -- or onto another address -- needs review."""
    _check_version(v, expected_version)
    _editable(v)
    if not v.address_confirmed_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "Confirm the address first")
    pack = get_pack(db, v.country_code)
    if action == "confirm":
        if v.original_latitude is None:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "We couldn't place this address on the map. Place the marker on the property instead.")
        if "HOUSE_NUMBER_NOT_ON_MAP" in (v.reason_codes or []):
            # The point is another building; "This is correct" would record it as the property.
            raise HTTPException(status.HTTP_409_CONFLICT, REASONS["HOUSE_NUMBER_NOT_ON_MAP"][0])
        v.confirmed_latitude, v.confirmed_longitude = v.original_latitude, v.original_longitude
        v.pin_moved_meters = 0.0
        v.pin_status = "AUTO_CONFIRMED" if v.location_precision in loc.AUTO_ACCEPT_PRECISIONS and \
            v.geocode_status == "RESOLVED" else "USER_CONFIRMED"
        v.reason_codes = [c for c in (v.reason_codes or []) if c != "PIN_MOVED_TOO_FAR"]
        _event(db, "PIN_CONFIRMED", v, actor=user, correlation_id=correlation_id, extra={"pinStatus": v.pin_status})
    elif action == "adjust":
        if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the marker's new position")
        if not reason.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tell us why the marker needs to move")
        v.confirmed_latitude, v.confirmed_longitude = float(latitude), float(longitude)
        v.pin_adjust_count += 1
        v.pin_adjust_reason = reason.strip()[:300]
        if device:
            v.pin_adjust_device = {**device, "at": _now().isoformat(), "adjustment": v.pin_adjust_count}
        review = False
        if v.original_latitude is None:
            v.pin_moved_meters = None
            review = True  # placed by hand with no provider result to compare against
        else:
            v.pin_moved_meters = round(loc.distance_meters(v.original_latitude, v.original_longitude, latitude, longitude), 1)
            # A house-level map point allows only small corrections; an
            # area-level one (no house number on the map) is only a starting
            # point, so the host may place the marker anywhere in the area.
            if v.location_precision in loc.AUTO_ACCEPT_PRECISIONS:
                allowed = pack.pin_move_review_meters
            elif v.location_precision == "STREET":
                allowed = max(pack.pin_move_review_meters, STREET_PIN_MOVE_METERS)
            else:
                allowed = max(pack.pin_move_review_meters, APPROXIMATE_PIN_MOVE_METERS)
            review = v.pin_moved_meters > allowed
        try:
            reversed_address, _code = loc.reverse(latitude, longitude)
        except loc.LocationUnavailable:
            reversed_address = None
        if reversed_address is not None:
            v.pin_reverse_geocode = reversed_address.formatted[:500] or reversed_address.one_line()[:500]
            canonical = v.canonical_address or {}
            # "Another address" = another postal area. Compared at area level
            # per country (a UK postcode covers ~15 houses, so a few metres to
            # the front door can cross one), and only for a real move or a
            # hand-placed pin. Town / village names aren't compared when both
            # postal codes are known: map data names the same place
            # differently (e.g. "Madupalli" vs "Madhira").
            country = canonical.get("country_code") or v.country_code
            pin_area = loc.postal_area(reversed_address.postal_code, country)
            our_area = loc.postal_area(canonical.get("postal_code"), country)
            # Where postal codes are fine-grained, a few metres can cross one
            # even at area level (e.g. 221B Baker Street sits on NW1 / W1U);
            # area-level codes (India's PIN) count at any distance.
            real_move = (country not in loc.FINE_GRAINED_POSTAL_COUNTRIES or v.pin_moved_meters is None
                         or v.pin_moved_meters > SMALL_PIN_MOVE_METERS)
            if pin_area and our_area:
                cross_address = real_move and pin_area != our_area
            else:
                cross_address = real_move and bool(
                    reversed_address.locality and canonical.get("locality")
                    and _norm(reversed_address.locality) != _norm(canonical["locality"]))
            review = review or bool(cross_address)
        if v.pin_adjust_count >= 4:
            review = True  # repeated edits are a signal (Section 8)
        v.pin_status = "REVIEW_REQUIRED" if review else "ADJUSTED"
        # Every adjustment is kept (Section 6.4); the latest one never
        # overwrites the earlier ones or the provider's original result.
        v.pin_adjust_history = [*(v.pin_adjust_history or []), {
            "at": _now().isoformat(), "latitude": float(latitude), "longitude": float(longitude),
            "movedMeters": v.pin_moved_meters, "reverseGeocode": v.pin_reverse_geocode, "reason": v.pin_adjust_reason,
            "reviewRequired": review, "device": device or {},
        }][-20:]
        codes = [c for c in (v.reason_codes or []) if c != "PIN_MOVED_TOO_FAR"]
        v.reason_codes = codes + (["PIN_MOVED_TOO_FAR"] if review else [])
        _event(db, "PIN_ADJUSTED", v, actor=user, correlation_id=correlation_id,
               reason_codes=["PIN_MOVED_TOO_FAR"] if review else [],
               extra={"pinStatus": v.pin_status, "movedMeters": v.pin_moved_meters,
                      "withinPolicy": not review, "adjustCount": v.pin_adjust_count})
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "action must be confirm or adjust")
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


# -- Step 4: unit ----------------------------------------------------------------

def _find_duplicate(db: Session, v: PropertyLocationVerification) -> int | None:
    other = db.scalar(select(PropertyLocationVerification.property_id).where(
        PropertyLocationVerification.address_fingerprint == v.address_fingerprint,
        PropertyLocationVerification.property_id != v.property_id,
        PropertyLocationVerification.state.notin_(("REJECTED", "INVALIDATED")),
    ).limit(1))
    if other is not None:
        return other
    canonical = v.canonical_address or {}
    candidates = db.scalars(select(Property).where(
        Property.id != v.property_id, Property.country_code == canonical.get("country_code", ""),
        func.lower(Property.postal_code) == (canonical.get("postal_code") or "").lower(),
    ).limit(50))
    for prop in candidates:
        fp = fingerprint({"country_code": prop.country_code, "postal_code": prop.postal_code,
                          "address_line_1": prop.address_line_1, "locality": prop.locality}, prop.subpremise)
        if fp == v.address_fingerprint:
            return prop.id
    return None


def set_unit(db: Session, user: UserAccount, v: PropertyLocationVerification, *, property_kind: str,
             building_name: str = "", unit: str = "", floor: str = "", expected_version: int | None = None,
             correlation_id: str = "") -> PropertyLocationVerification:
    """Screen 4: the minimum details that distinguish this property from
    others at the same address, plus duplicate-property matching."""
    _check_version(v, expected_version)
    _editable(v)
    kind = (property_kind or "").upper()
    if kind not in PROPERTY_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"propertyKind must be one of {PROPERTY_KINDS}")
    if not v.address_confirmed_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "Confirm the address first")
    pack = get_pack(db, v.country_code)
    if kind in (pack.unit_required_for or []) and not unit.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, describe(["UNIT_MISSING"])["message"])
    v.property_kind, v.building_name = kind, building_name.strip()[:200]
    v.unit, v.floor = unit.strip()[:50], floor.strip()[:20]
    v.canonical_address = {**(v.canonical_address or {}), "subpremise": v.unit}
    v.address_fingerprint = fingerprint(v.canonical_address, v.unit)
    v.duplicate_of_property_id = _find_duplicate(db, v)
    v.reason_codes = [c for c in (v.reason_codes or []) if c not in ("POSSIBLE_DUPLICATE", "UNIT_MISSING")] + (
        ["POSSIBLE_DUPLICATE"] if v.duplicate_of_property_id else [])
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


DUPLICATE_ANSWERS = ("NOT_SAME_PROPERTY", "SAME_PROPERTY")


def answer_duplicate(db: Session, user: UserAccount, v: PropertyLocationVerification, *, answer: str, note: str = "",
                     expected_version: int | None = None, correlation_id: str = "") -> PropertyLocationVerification:
    """Screen 4: the host answers the possible-duplicate match. Either way a
    reviewer still checks it (Section 14) -- the answer is context, never an
    override."""
    _check_version(v, expected_version)
    _editable(v)
    if not v.duplicate_of_property_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "No possible duplicate was found for this property")
    answer = (answer or "").upper()
    if answer not in DUPLICATE_ANSWERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"answer must be one of {DUPLICATE_ANSWERS}")
    if answer == "NOT_SAME_PROPERTY" and not note.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tell us how this property differs (e.g. a different flat number)")
    v.duplicate_host_answer, v.duplicate_host_note = answer, note.strip()[:500]
    _event(db, "DUPLICATE_ANSWERED", v, actor=user, correlation_id=correlation_id, extra={"answer": answer})
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


POSTAL_PATTERNS = {
    "IN": r"^[1-9][0-9]{5}$",
    "US": r"^[0-9]{5}(-[0-9]{4})?$",
    "GB": r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$",
    "CA": r"^[A-Z][0-9][A-Z] ?[0-9][A-Z][0-9]$",
    "AU": r"^[0-9]{4}$",
}
NEARBY_CONFLICT_METERS = 15.0


def _postal_code_invalid(canonical: dict) -> bool:
    """Section 14 "impossible address": a postal code that can't exist in the
    property's country."""
    import re

    pattern = POSTAL_PATTERNS.get((canonical.get("country_code") or "").upper())
    code = (canonical.get("postal_code") or "").strip().upper()
    if code.replace(" ", "") == "GIR0AA":
        return False  # a real, special UK postcode
    return bool(pattern and code and not re.match(pattern, code))


def _nearby_conflict(db: Session, v: PropertyLocationVerification) -> int | None:
    """Section 8 duplicate/fraud: another owner's verified property within a
    few metres but at a different address (pin pointed at someone else's home)."""
    if v.confirmed_latitude is None or v.confirmed_longitude is None:
        return None
    delta = 0.0005  # ~55 m box, then exact distance
    rows = db.scalars(select(PropertyLocationVerification).where(
        PropertyLocationVerification.property_id != v.property_id,
        PropertyLocationVerification.state.in_(("VERIFIED", "MANUAL_REVIEW", "IN_PROGRESS")),
        PropertyLocationVerification.confirmed_latitude.between(v.confirmed_latitude - delta, v.confirmed_latitude + delta),
        PropertyLocationVerification.confirmed_longitude.between(v.confirmed_longitude - delta, v.confirmed_longitude + delta),
    ).limit(20))
    for other in rows:
        if other.party_id == v.party_id or other.address_fingerprint == v.address_fingerprint:
            continue
        if other.unit and v.unit and other.unit != v.unit and (other.canonical_address or {}).get("address_line_1") == \
                (v.canonical_address or {}).get("address_line_1"):
            continue  # same building, different units (Section 20)
        if loc.distance_meters(v.confirmed_latitude, v.confirmed_longitude,
                               other.confirmed_latitude, other.confirmed_longitude) <= NEARBY_CONFLICT_METERS:
            return other.property_id
    return None


# -- Step 5: evidence ------------------------------------------------------------

EVIDENCE_FILE_CATEGORY = "property_location"


def read_evidence(evidence: PropertyLocationEvidence) -> bytes | None:
    from sqlalchemy.orm import object_session

    from app.core import file_store
    from app.core.field_encryption import decrypt_bytes

    if not evidence.stored_filename:
        return None
    data = file_store.read(object_session(evidence), EVIDENCE_FILE_CATEGORY, evidence.stored_filename)
    return decrypt_bytes(data) if data is not None else None


def add_evidence(db: Session, user: UserAccount, v: PropertyLocationVerification, *, evidence_type: str,
                 content: bytes, original_filename: str, expected_version: int | None = None,
                 correlation_id: str = "") -> PropertyLocationEvidence:
    """Screen 5: upload property-existence evidence. Encrypted at rest; the
    file hash detects reuse across properties (Section 14)."""
    from app.core.field_encryption import encrypt_bytes
    from app.core.identity_uploads import _sniff, _strip_image_metadata

    _check_version(v, expected_version)
    _editable(v)
    pack = get_pack(db, v.country_code)
    if evidence_type not in (pack.accepted_evidence_types or []):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This document type isn't accepted for properties in this country. Choose another.")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty")
    if len(content) > settings.property_verification_document_max_size_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"File exceeds the {settings.property_verification_document_max_size_mb}MB limit")
    sniffed = _sniff(content)
    if not sniffed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported file -- upload a PDF, JPG or PNG")
    extension, content_type = sniffed
    from app.core import upload_scan
    from app.services.authority_service import _tamper_signal

    scan_status = upload_scan.inspect(content, content_type)  # Section 14 / P0 #11: unsafe files rejected
    tamper = _tamper_signal(content, content_type)
    content = _strip_image_metadata(content, extension)
    if len(v.evidence) >= 10:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can add up to 10 documents")

    sha = hashlib.sha256(content).hexdigest()
    from app.core import file_store

    stored = file_store.put(
        db, EVIDENCE_FILE_CATEGORY, encrypt_bytes(content), extension=f"{extension}.enc",
        content_type=content_type, encrypted=True,
    )
    reused = db.scalar(select(func.count(PropertyLocationEvidence.id)).join(PropertyLocationVerification).where(
        PropertyLocationEvidence.sha256 == sha, PropertyLocationVerification.property_id != v.property_id)) or 0
    from app.services.authority_service import _verified_legal_name
    from app.services.property_document_analysis import analyze

    a = analyze(content, content_type, evidence_type=evidence_type, canonical_address=v.canonical_address or {},
                unit=v.unit, owner_name=_verified_legal_name(db, v.party_id))
    evidence = PropertyLocationEvidence(
        verification_id=v.id, evidence_type=evidence_type, stored_filename=stored,
        original_filename=Path(original_filename or "document").name[:255], content_type=content_type,
        file_size=len(content), sha256=sha, readable=a.readable, address_matched=a.address_matched,
        unit_matched=a.unit_matched, reused_elsewhere=reused > 0, text_source=a.text_source,
        ocr_confidence=a.ocr_confidence, quality=a.quality, postal_matched=a.postal_matched,
        owner_name_matched=a.owner_name_matched, document_type_matched=a.document_type_matched,
        document_year=a.document_year, signals=a.signals,
        reference_number=(a.reference_number + (f" (council tax band {a.council_tax_band})" if a.council_tax_band else ""))[:40],
        scan_status=scan_status, tamper_signal=tamper,
    )
    db.add(evidence)
    db.flush()
    _event(db, "PROPERTY_EVIDENCE_UPLOADED", v, actor=user, correlation_id=correlation_id,
           reason_codes=["EVIDENCE_REUSED"] if reused else [],
           extra={"evidenceId": evidence.id, "evidenceType": evidence_type, "readable": evidence.readable,
                  "textSource": evidence.text_source, "quality": evidence.quality})
    _touch(v)
    db.commit()
    db.refresh(evidence)
    return evidence


def remove_evidence(db: Session, user: UserAccount, v: PropertyLocationVerification, evidence_id: int, *,
                    expected_version: int | None = None) -> None:
    _check_version(v, expected_version)
    _editable(v)
    evidence = db.get(PropertyLocationEvidence, evidence_id)
    if evidence is None or evidence.verification_id != v.id or evidence.removed_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if evidence.stored_filename:
        from app.core import file_store

        file_store.delete(db, EVIDENCE_FILE_CATEGORY, evidence.stored_filename)
    # Soft delete: the file goes, the hash and match results stay so the same
    # document is still caught if it's reused elsewhere (Section 14).
    evidence.stored_filename = None
    evidence.removed_at = _now()
    _event(db, "PROPERTY_EVIDENCE_REMOVED", v, actor=user, extra={"evidenceId": evidence.id})
    _touch(v)
    db.commit()


# -- Steps 6-7: submit & automated decision --------------------------------------

def _source_check(db: Session, v: PropertyLocationVerification, user: UserAccount, correlation_id: str) -> str:
    """Registry / authoritative source check (Section 6.6). No registry
    integration is contracted yet, so the result is UNAVAILABLE and the
    decision relies on document corroboration (P1 registry expansion)."""
    v.source_check = "UNAVAILABLE"
    _event(db, "PROPERTY_SOURCE_CHECKED", v, actor=user, correlation_id=correlation_id,
           extra={"result": v.source_check})
    return v.source_check


def decide(v: PropertyLocationVerification, pack: PropertyRegulatoryPack) -> tuple[str, str, list[str]]:
    """Automatic decision -> (state, existence_status, reason codes).

    Property existence is decided from the document evidence, read by OCR or
    from the PDF's own text (services/property_document_analysis.py). The
    result of reading the document is always automatic -- VERIFIED, or
    ACTION_REQUIRED with one clear thing for the host to fix -- never a wait for a reviewer.
    Fraud / duplicate signals are different (Sections 14, 20): reused
    evidence, a possible duplicate the host says is a different property, a
    conflicting property at the same spot, an edited document or a property
    rejected before go to MANUAL_REVIEW:

    - a clear, recent primary document (tax / registry / title / building
      record) of the chosen type that shows the confirmed address and postal
      code (and the unit, when one is given) verifies the property, even when
      the map provider couldn't place it precisely (Section 7.1: no
      auto-reject just because a provider lacks coverage);
    - anything that can't be confirmed asks the host for the specific fix.
    """
    action: list[str] = []
    review: list[str] = []
    evidence = list(v.evidence)
    primary = [e for e in evidence if e.evidence_type not in SUPPLEMENTARY_EVIDENCE_TYPES]
    good = [e for e in primary if e.quality != "POOR" and e.readable]

    # -- address / unit integrity
    if v.property_kind in (pack.unit_required_for or []) and not v.unit:
        action.append("UNIT_MISSING")
    if _postal_code_invalid(v.canonical_address or {}):
        action.append("POSTAL_CODE_INVALID")

    # -- document evidence
    if not evidence:
        action.append("EVIDENCE_UNREADABLE")
    elif not primary:
        action.append("SUPPLEMENTARY_EVIDENCE_ONLY")
    elif not good:
        action.append("EVIDENCE_POOR_QUALITY" if any(e.quality == "POOR" for e in primary) else "EVIDENCE_UNREADABLE")
    matched = [e for e in good if e.address_matched]
    if good and not matched:
        action.append("EVIDENCE_MISMATCH")
    current = [e for e in matched if "EVIDENCE_OUTDATED" not in (e.signals or [])]
    if matched and not current:
        action.append("EVIDENCE_OUTDATED")
    typed = [e for e in current if e.document_type_matched is not False]
    if current and not typed:
        action.append("EVIDENCE_TYPE_UNCLEAR")
    if v.unit and typed and not any(e.unit_matched for e in typed):
        action.append("UNIT_NOT_CONFIRMED")

    # -- fraud / duplicate signals: a reviewer resolves them (Sections 14, 20)
    if any(e.reused_elsewhere for e in evidence):
        review.append("EVIDENCE_REUSED")
    if any(e.tamper_signal or e.scan_status == "ERROR" for e in evidence):
        review.append("EVIDENCE_REVIEW")
    if v.duplicate_of_property_id:
        if v.duplicate_host_answer == "SAME_PROPERTY":
            action.append("SAME_PROPERTY_DECLARED")
        elif v.duplicate_host_answer == "NOT_SAME_PROPERTY":
            review.append("DUPLICATE_REVIEW")  # the host's answer is checked, not trusted
        else:
            action.append("POSSIBLE_DUPLICATE")
    if "NEARBY_PROPERTY_CONFLICT" in (v.reason_codes or []):
        review.append("NEARBY_PROPERTY_CONFLICT")
    if "PREVIOUSLY_REJECTED" in (v.reason_codes or []):
        review.append("PREVIOUSLY_REJECTED")
    if "ADDRESS_COMPONENTS_CONFLICT" in (v.reason_codes or []):
        action.append("ADDRESS_COMPONENTS_CONFLICT")
    # A marker dragged far from (or onto another address than) where the map
    # placed it (Section 14). A pin placed by hand because the map couldn't
    # find the address at all is allowed -- the document confirms it.
    if v.pin_status == "REVIEW_REQUIRED" and v.original_latitude is not None:
        action.append("PIN_MOVED_TOO_FAR")

    if action:
        return "ACTION_REQUIRED", "INSUFFICIENT", list(dict.fromkeys(action + review))
    if review:
        return "MANUAL_REVIEW", "INSUFFICIENT", list(dict.fromkeys(review))
    return "VERIFIED", "EVIDENCE_CONFIRMED", []


def _reread_unmatched_evidence(db: Session, v: PropertyLocationVerification) -> None:
    """Documents read against an earlier address, or before the matching
    improved (e.g. landmark words were required), are read again so a
    stale "doesn't match" doesn't stick. Only the outcomes are stored."""
    from app.services.authority_service import _verified_legal_name
    from app.services.property_document_analysis import analyze

    for e in v.evidence:
        if e.address_matched is not False or not e.stored_filename:
            continue
        content = read_evidence(e)
        if content is None:
            continue
        a = analyze(content, e.content_type, evidence_type=e.evidence_type, canonical_address=v.canonical_address or {},
                    unit=v.unit, owner_name=_verified_legal_name(db, v.party_id))
        e.readable, e.address_matched, e.unit_matched = a.readable, a.address_matched, a.unit_matched
        e.postal_matched, e.owner_name_matched, e.document_type_matched = (
            a.postal_matched, a.owner_name_matched, a.document_type_matched)
        e.quality, e.signals = a.quality, a.signals


def _previously_rejected(db: Session, v: PropertyLocationVerification) -> bool:
    """Section 8 historical consistency: this property / address + unit was
    rejected by a reviewer before."""
    query = select(func.count(PropertyLocationVerification.id)).where(
        PropertyLocationVerification.id != v.id, PropertyLocationVerification.state == "REJECTED")
    match = PropertyLocationVerification.property_id == v.property_id
    if v.address_fingerprint:
        match = match | (PropertyLocationVerification.address_fingerprint == v.address_fingerprint)
    return (db.scalar(query.where(match)) or 0) > 0


def submit(db: Session, user: UserAccount, v: PropertyLocationVerification, *, attested: bool,
           idempotency_key: str | None = None, expected_version: int | None = None,
           correlation_id: str = "") -> PropertyLocationVerification:
    if idempotency_key and v.submit_idempotency_key == idempotency_key:
        return v  # a retried submit returns the same result
    _check_version(v, expected_version)
    if not attested:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Confirm this information accurately identifies the property you intend to list")
    _editable(v)
    missing = []
    if not v.address_confirmed_at:
        missing.append("confirm the address")
    if not v.pin_status:
        missing.append("confirm the location")
    if not v.property_kind:
        missing.append("add the property details")
    if not v.evidence:
        missing.append("upload property evidence")
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Before submitting, " + ", ".join(missing) + ".")
    pack = get_pack(db, v.country_code)
    _reread_unmatched_evidence(db, v)
    _source_check(db, v, user, correlation_id)
    nearby = _nearby_conflict(db, v)
    rejected_before = _previously_rejected(db, v)
    v.reason_codes = [c for c in (v.reason_codes or []) if c not in ("NEARBY_PROPERTY_CONFLICT", "PREVIOUSLY_REJECTED")] + (
        ["NEARBY_PROPERTY_CONFLICT"] if nearby else []) + (["PREVIOUSLY_REJECTED"] if rejected_before else [])
    previous = v.state
    state, existence, codes = decide(v, pack)
    now = _now()
    v.attested_at = v.submitted_at = now
    v.submit_idempotency_key = idempotency_key
    v.existence_status = existence
    v.reason_codes = codes
    _write_canonical_to_property(db, v, user=user, correlation_id=correlation_id)
    if state == "VERIFIED":
        _verify(db, v, pack, actor=user, correlation_id=correlation_id)
    elif state == "MANUAL_REVIEW":
        v.state = "MANUAL_REVIEW"
        _event(db, "PROPERTY_MANUAL_REVIEW_STARTED", v, actor=user, reason_codes=codes, previous_state=previous,
               new_state=v.state, correlation_id=correlation_id)
        _notify_reviewers(db, v)
    else:
        v.state = "ACTION_REQUIRED"
        v.decided_at = now
        _event(db, "PROPERTY_ACTION_REQUIRED", v, actor=user, reason_codes=codes, previous_state=previous,
               new_state=v.state, correlation_id=correlation_id)
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


def _verify(db: Session, v: PropertyLocationVerification, pack: PropertyRegulatoryPack, *,
            actor: UserAccount | AdminUser | None, correlation_id: str) -> None:
    previously = db.scalar(select(func.count(PropertyLocationVerification.id)).where(
        PropertyLocationVerification.property_id == v.property_id,
        PropertyLocationVerification.id != v.id,
        PropertyLocationVerification.verified_at.is_not(None))) or 0
    previous_state = v.state
    now = _now()
    v.state = "VERIFIED"
    v.decided_at = v.verified_at = now
    v.expires_at = now + timedelta(days=pack.validity_days or 365)
    _event(db, "PROPERTY_REVERIFIED" if previously else "PROPERTY_VERIFIED", v, actor=actor,
           previous_state=previous_state, new_state="VERIFIED", correlation_id=correlation_id,
           extra={"existenceStatus": v.existence_status})
    from app.crud import notification as notif_crud

    notif_crud.notify_user_by_party(
        db, v.party_id, title="Property verified",
        message="Your property's address and existence checks are complete.",
        notification_type="property_verification.verified",
        related_entity_type=RESOURCE, related_entity_id=str(v.id),
    )
    _recheck_waiting_authority(db, v.property_id, correlation_id)


def _recheck_waiting_authority(db: Session, property_id: int, correlation_id: str) -> None:
    """Authority cases waiting only on this property's verification are
    decided straight away rather than at the next scheduler run. Best
    effort: a failure here never undoes the property verification."""
    from app.services.authority_service import recheck_pending

    db.flush()
    try:
        with db.begin_nested():
            recheck_pending(db, property_id=property_id, correlation_id=correlation_id)
    except Exception:
        logger.exception("authority re-check after property verification failed")


def _notify_reviewers(db: Session, v: PropertyLocationVerification) -> None:
    from app.crud import notification as notif_crud

    notif_crud.notify_all_super_admins(
        db, title="Property verification needs review",
        message=f"Property verification #{v.id} (property #{v.property_id}) needs a reviewer: "
                f"{', '.join(v.reason_codes or []) or 'manual review'}.",
        notification_type="property_verification.review",
        related_entity_type=RESOURCE, related_entity_id=str(v.id),
    )


def _write_canonical_to_property(db: Session, v: PropertyLocationVerification, *, user: UserAccount,
                                 correlation_id: str) -> None:
    """The confirmed canonical address/location becomes the property's
    record. A material change invalidates other verifications of this
    property (Section 13.3)."""
    from app.crud.property_verification import invalidate_for_address_change, is_material_address_change

    prop = db.get(Property, v.property_id)
    c = v.canonical_address or {}
    line1 = c.get("address_line_1", "")
    display = ", ".join(p for p in (line1, c.get("address_line_2", "")) if p) or prop.address
    changed = is_material_address_change(
        {"address": prop.address, "city": prop.city, "landmark": prop.landmark, "jurisdiction_code": prop.jurisdiction_code},
        {"address": display, "city": c.get("locality") or prop.city, "landmark": prop.landmark,
         "jurisdiction_code": prop.jurisdiction_code},
    )
    prop.address, prop.city = display[:500], (c.get("locality") or prop.city)[:255]
    prop.address_line_1, prop.address_line_2 = line1[:300], c.get("address_line_2", "")[:300]
    prop.subpremise, prop.locality = v.unit[:50], c.get("locality", "")[:200]
    prop.administrative_area, prop.postal_code = c.get("administrative_area", "")[:200], c.get("postal_code", "")[:20]
    prop.country_code = (c.get("country_code") or v.country_code)[:2]
    prop.canonical_formatted_address = (c.get("formatted") or loc.CanonicalAddress.from_dict(c).one_line())[:600]
    prop.property_kind, prop.building_name, prop.floor = v.property_kind, v.building_name, v.floor
    prop.latitude_private, prop.longitude_private = v.confirmed_latitude, v.confirmed_longitude
    prop.location_precision, prop.geocode_status, prop.pin_status = v.location_precision, v.geocode_status, v.pin_status
    pack = get_pack(db, prop.country_code)
    prop.public_location_decimals = pack.public_location_decimals
    if v.provider:
        # Store provider references only where terms allow (place IDs are storable).
        prop.provider_refs = [{"provider": v.provider, "providerPlaceId": v.provider_place_id,
                               "termsStorageClass": "PLACE_ID" if v.provider == "google" else "OPEN_DATA",
                               "checkedAt": (v.provider_checked_at or _now()).isoformat()}]
    prop.location_version += 1
    # Section 20 transliteration: keep the host's own wording (local script)
    # when the provider normalized it into a different form.
    submitted = v.submitted_address or {}
    local = ", ".join(x for x in (submitted.get("address_line_1"), submitted.get("address_line_2"),
                                  submitted.get("locality")) if x)
    prop.address_local = (local if local and _norm(local) != _norm(display + ", " + prop.city) else "")[:600]
    if changed:
        from app.services.authority_service import reopen_for_address_change

        invalidate_for_address_change(db, prop.id, actor_user_id=user.id, correlation_id=correlation_id)
        invalidate_property(db, prop.id, reason="ADDRESS_CHANGED", except_id=v.id, correlation_id=correlation_id)
        # Section 13.3: authority for the old address doesn't silently transfer.
        reopen_for_address_change(db, prop.id, correlation_id=correlation_id)


def invalidate_property(db: Session, property_id: int, *, reason: str = "ADDRESS_CHANGED", except_id: int | None = None,
                        correlation_id: str = "") -> int:
    """A material address/location change reopens verification: verified,
    open and in-review sessions become INVALIDATED (history kept)."""
    rows = list(db.scalars(select(PropertyLocationVerification).where(
        PropertyLocationVerification.property_id == property_id,
        PropertyLocationVerification.state.in_(("VERIFIED", "IN_PROGRESS", "ACTION_REQUIRED", "MANUAL_REVIEW")),
        *( [PropertyLocationVerification.id != except_id] if except_id else [] ),
    )))
    for v in rows:
        previous = v.state
        v.state = "INVALIDATED"
        v.reason_codes = [reason]
        _touch(v)
        _event(db, "PROPERTY_VERIFICATION_INVALIDATED", v, reason_codes=[reason], previous_state=previous,
               new_state="INVALIDATED", correlation_id=correlation_id)
    if rows:
        db.flush()
    return len(rows)


# -- Trust & Safety review (Section 16) -------------------------------------------

FOUR_EYES_CODES = ("EVIDENCE_REUSED", "EVIDENCE_REVIEW", "DUPLICATE_REVIEW", "NEARBY_PROPERTY_CONFLICT",
                   "PREVIOUSLY_REJECTED")


def assign(db: Session, admin: AdminUser, v: PropertyLocationVerification, *, release: bool = False,
           correlation_id: str = "") -> PropertyLocationVerification:
    """Section 16: least privilege -- one reviewer holds a case at a time;
    taking a case someone else holds reassigns it (audited)."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    if release:
        if v.assigned_admin_id not in (None, admin.id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Only the assigned reviewer can release this case")
        v.assigned_admin_id, v.assigned_at = None, None
        _event(db, "PROPERTY_REVIEW_RELEASED", v, actor=admin, correlation_id=correlation_id)
    else:
        if v.state != "MANUAL_REVIEW":
            raise HTTPException(status.HTTP_409_CONFLICT, "Only a case in review can be assigned")
        previous = v.assigned_admin_id
        v.assigned_admin_id, v.assigned_at = admin.id, _now()
        _event(db, "PROPERTY_REVIEW_ASSIGNED", v, actor=admin, correlation_id=correlation_id,
               extra={"reassignedFrom": previous} if previous and previous != admin.id else None)
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


def require_assignment(db: Session, admin: AdminUser, v: PropertyLocationVerification, *,
                       correlation_id: str = "") -> None:
    """Opening evidence or deciding needs the case; an unassigned case in
    review is taken by the reviewer who opens it."""
    if v.assigned_admin_id == admin.id:
        return
    if v.assigned_admin_id is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This case is assigned to another reviewer -- reassign it to yourself first")
    if v.state == "MANUAL_REVIEW":
        assign(db, admin, v, correlation_id=correlation_id)
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Evidence is only available for a case assigned to you")


def review(db: Session, admin: AdminUser, v: PropertyLocationVerification, *, decision: str, reason_code: str,
           note: str = "", correlation_id: str = "") -> PropertyLocationVerification:
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    decision = decision.upper()
    if decision not in REVIEW_DECISIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"decision must be one of {REVIEW_DECISIONS}")
    if reason_code not in REVIEW_REASONS[decision]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reasonCode must be one of {REVIEW_REASONS[decision]}")
    if v.state != "MANUAL_REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This verification is {v.state} and can't be reviewed")
    if decision == "APPROVE" and v.first_approver_admin_id is not None and v.first_approver_admin_id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "A second, different reviewer must approve this property")
    require_assignment(db, admin, v, correlation_id=correlation_id)
    previous = v.state
    v.reviewer_admin_id = admin.id
    v.review_reason_code = reason_code
    v.review_note = note.strip()[:1000]
    pack = get_pack(db, v.country_code)
    if decision == "APPROVE":
        # Four-eyes for duplicate / fraud cases (Section 16 high-risk overrides).
        needs_two = bool(v.duplicate_of_property_id) or any(c in (v.reason_codes or []) for c in FOUR_EYES_CODES)
        if needs_two and v.first_approver_admin_id is None:
            v.first_approver_admin_id = admin.id
            v.assigned_admin_id, v.assigned_at = None, None  # free for the second reviewer
            v.reason_codes = list(dict.fromkeys([*(v.reason_codes or []), "SECOND_APPROVAL_REQUIRED"]))
            _event(db, "PROPERTY_REVIEW_FIRST_APPROVAL", v, actor=admin, reason_codes=[reason_code],
                   previous_state=previous, new_state=v.state, correlation_id=correlation_id)
            _touch(v)
            db.commit()
            db.refresh(v)
            return v
        if v.first_approver_admin_id is not None and v.first_approver_admin_id == admin.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "A second, different reviewer must approve this property")
        v.existence_status = "MANUAL_CONFIRMED"
        v.reason_codes = []
        _verify(db, v, pack, actor=admin, correlation_id=correlation_id)
    elif decision == "REJECT":
        v.state = "REJECTED"
        v.decided_at = _now()
        v.reason_codes = [reason_code]
        _event(db, "PROPERTY_REJECTED", v, actor=admin, reason_codes=[reason_code], previous_state=previous,
               new_state="REJECTED", correlation_id=correlation_id)
    else:
        v.state = "ACTION_REQUIRED"
        v.decided_at = _now()
        v.reason_codes = [reason_code]
        _event(db, "PROPERTY_ACTION_REQUIRED", v, actor=admin, reason_codes=[reason_code], previous_state=previous,
               new_state="ACTION_REQUIRED", correlation_id=correlation_id)
    if decision != "APPROVE":
        from app.crud import notification as notif_crud

        notif_crud.notify_user_by_party(
            db, v.party_id, title="Property verification update", message=describe([reason_code])["message"],
            notification_type="property_verification.updated", related_entity_type=RESOURCE, related_entity_id=str(v.id),
        )
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


def restart(db: Session, user: UserAccount, v: PropertyLocationVerification, *, correlation_id: str = "") -> PropertyLocationVerification:
    """A new session after REJECTED / EXPIRED / INVALIDATED (renew / resubmit)."""
    if effective_state(v) not in ("REJECTED", "EXPIRED", "INVALIDATED", "EXPIRING_SOON"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This verification can't be restarted -- continue it instead")
    return start(db, user, v.property_id, correlation_id=correlation_id)


# -- jobs ------------------------------------------------------------------------

def sweep_expired(db: Session) -> int:
    """Section 7: EXPIRED once the validity ends (live listings on the
    property pause -- the publish gate no longer passes), and a one-time
    "expiring soon" notice inside the pack's renewal window."""
    from app.crud import notification as notif_crud

    now = _now()
    rows = list(db.scalars(select(PropertyLocationVerification).where(
        PropertyLocationVerification.state == "VERIFIED", PropertyLocationVerification.expires_at.is_not(None))))
    expired = 0
    for v in rows:
        expires = _utc(v.expires_at)
        if expires <= now:
            v.state = "EXPIRED"
            v.reason_codes = ["VERIFICATION_EXPIRED"]
            _touch(v)
            _event(db, "PROPERTY_VERIFICATION_EXPIRED", v, reason_codes=["VERIFICATION_EXPIRED"],
                   previous_state="VERIFIED", new_state="EXPIRED")
            db.flush()
            paused = _pause_listings(db, v.property_id, "Property verification expired")
            notif_crud.notify_user_by_party(
                db, v.party_id, title="Property verification expired",
                message=REASONS["VERIFICATION_EXPIRED"][0] + (f" {paused} listing(s) paused." if paused else ""),
                notification_type="property_verification.expired", related_entity_type=RESOURCE,
                related_entity_id=str(v.id))
            expired += 1
        elif (expires - now <= timedelta(days=_expiring_soon_days(db, v.country_code))
              and v.expiring_notified_at is None):
            v.expiring_notified_at = now
            _event(db, "PROPERTY_VERIFICATION_EXPIRING", v, extra={"expiresAt": expires.isoformat()})
            notif_crud.notify_user_by_party(
                db, v.party_id, title="Property verification expiring soon",
                message=f"Your property's verification expires on {expires:%d %b %Y}. Renew it to keep listing.",
                notification_type="property_verification.expiring", related_entity_type=RESOURCE,
                related_entity_id=str(v.id))
    db.commit()
    return expired


def _pause_listings(db: Session, property_id: int, reason: str) -> int:
    """Section 11.2: expired / invalidated verification blocks publication --
    live listings on the property are suspended (the publish gate re-checks
    before they can go live again)."""
    from app.crud.listing import suspend_listing
    from app.crud.property_verification import get_valid_property_verification_for_room
    from app.models.listing import Listing
    from app.models.room import Room

    room_ids = list(db.scalars(select(Room.id).where(Room.property_id == property_id)))
    count = 0
    for listing in db.scalars(select(Listing).where(Listing.room_id.in_(room_ids), Listing.state == "PUBLISHED")):
        if get_valid_property_verification_for_room(db, listing.room_id) is None:
            suspend_listing(db, listing, reason)
            count += 1
    return count


def purge_expired_evidence(db: Session) -> int:
    """Deletes evidence files past the pack's retention after the decision;
    the decision, hash and match results stay."""
    purged = 0
    for evidence in db.scalars(select(PropertyLocationEvidence).where(
            PropertyLocationEvidence.purged_at.is_(None), PropertyLocationEvidence.stored_filename.is_not(None))):
        v = evidence.verification
        pack = get_pack(db, v.country_code)
        decided = _utc(v.decided_at)
        if not pack.evidence_retention_days or decided is None or v.state in OPEN_STATES + ("MANUAL_REVIEW",):
            continue
        if decided + timedelta(days=pack.evidence_retention_days) <= _now():
            from app.core import file_store

            file_store.delete(db, EVIDENCE_FILE_CATEGORY, evidence.stored_filename)
            evidence.stored_filename = None
            evidence.purged_at = _now()
            purged += 1
    db.commit()
    return purged


# -- metrics (Section 18) --------------------------------------------------------

def metrics(db: Session, *, days: int = 30) -> dict:
    """Aggregates only -- no addresses, coordinates or documents."""
    from statistics import median

    since = _now() - timedelta(days=days)
    rows = list(db.scalars(select(PropertyLocationVerification).where(PropertyLocationVerification.created_at >= since)))
    submitted = [v for v in rows if v.submitted_at]

    def rate(part, whole):
        return round(100 * part / whole, 1) if whole else None

    precision: dict[str, int] = {}
    reasons: dict[str, int] = {}
    by_country: dict[str, dict] = {}
    for v in rows:
        if v.location_precision:
            precision[v.location_precision] = precision.get(v.location_precision, 0) + 1
        for code in v.reason_codes or []:
            reasons[code] = reasons.get(code, 0) + 1
        row = by_country.setdefault(v.country_code or "?", {"started": 0, "manual_entry": 0, "verified": 0})
        row["started"] += 1
        row["manual_entry"] += int(v.entry_mode == "MANUAL")
        row["verified"] += int(v.verified_at is not None)
    adjusted = [v for v in rows if v.pin_status in ("ADJUSTED", "REVIEW_REQUIRED")]
    moves = [v.pin_moved_meters for v in adjusted if v.pin_moved_meters is not None]
    auto_pass = [v for v in submitted if v.verified_at and v.reviewer_admin_id is None]
    reviewed = [v for v in submitted if v.state == "MANUAL_REVIEW" or v.reviewer_admin_id is not None]
    action = [v for v in submitted if v.state == "ACTION_REQUIRED"]
    durations = [(_utc(v.submitted_at) - _utc(v.created_at)).total_seconds() / 60 for v in submitted]
    # Section 18: abandonment by step -- sessions idle for 7+ days, by the
    # furthest step reached.
    stale_before = _now() - timedelta(days=7)
    abandoned: dict[str, int] = {}
    for v in rows:
        if v.state != "IN_PROGRESS" or _utc(v.updated_at or v.created_at) > stale_before:
            continue
        step = ("evidence" if v.property_kind else "unit" if v.pin_status else "location" if v.address_confirmed_at
                else "address")
        abandoned[step] = abandoned.get(step, 0) + 1
    by_provider: dict[str, dict] = {}
    for v in rows:
        row = by_provider.setdefault(v.provider or "manual", {"sessions": 0, "provider_errors": 0, "verified": 0})
        row["sessions"] += 1
        row["provider_errors"] += int("PROVIDER_UNAVAILABLE" in (v.reason_codes or []))
        row["verified"] += int(v.verified_at is not None)
    duplicates_reviewed = [v for v in rows if v.duplicate_of_property_id and v.decided_at]
    # Section 18: correction acceptance, from the ADDRESS_CONFIRMED events.
    from app.models.domain_event import DomainEvent

    confirmations = [e.payload or {} for e in db.scalars(select(DomainEvent).where(
        DomainEvent.resource_type == RESOURCE, DomainEvent.event_type == "ADDRESS_CONFIRMED",
        DomainEvent.occurred_at >= since))]
    offered = [c for c in confirmations if c.get("correctionOffered")]
    # Reviewer turnaround (hours from submission to the reviewer's decision).
    turnaround = [(_utc(v.decided_at) - _utc(v.submitted_at)).total_seconds() / 3600
                  for v in submitted if v.reviewer_admin_id and v.decided_at and v.submitted_at]
    # Action-required recovery: sessions that needed a fix and then verified.
    asked = [v for v in submitted if v.state == "ACTION_REQUIRED" or (
        v.verified_at and db.scalar(select(func.count(DomainEvent.id)).where(
            DomainEvent.resource_type == RESOURCE, DomainEvent.resource_id == str(v.id),
            DomainEvent.event_type == "PROPERTY_ACTION_REQUIRED")))]
    return {
        "period_days": days,
        "started": len(rows),
        "submitted": len(submitted),
        "verified": sum(1 for v in rows if v.verified_at),
        "manual_entry_rate": rate(sum(1 for v in rows if v.entry_mode == "MANUAL"), sum(1 for v in rows if v.entry_mode)),
        "correction_acceptance_rate": rate(sum(1 for c in offered if c.get("correctionAccepted")), len(offered)),
        "median_review_turnaround_hours": round(median(turnaround), 1) if turnaround else None,
        "action_required_recovery_rate": rate(sum(1 for v in asked if v.verified_at), len(asked)),
        "geocode_precision": precision,
        "pin_adjustment_rate": rate(len(adjusted), sum(1 for v in rows if v.pin_status)),
        "median_pin_move_meters": round(median(moves), 1) if moves else None,
        "auto_pass_rate": rate(len(auto_pass), len(submitted)),
        "manual_review_rate": rate(len(reviewed), len(submitted)),
        "action_required_count": len(action),
        "duplicate_flags": sum(1 for v in rows if v.duplicate_of_property_id),
        "pending_review": db.scalar(select(func.count(PropertyLocationVerification.id)).where(
            PropertyLocationVerification.state == "MANUAL_REVIEW")) or 0,
        "median_completion_minutes": round(median(durations), 1) if durations else None,
        "reason_codes": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "by_country": by_country,
        "provider": loc.capabilities()["provider"],
        "abandoned_by_step": abandoned,
        "by_provider": by_provider,
        "provider_error_count": sum(r["provider_errors"] for r in by_provider.values()),
        "duplicate_false_positive_rate": rate(sum(1 for v in duplicates_reviewed if v.verified_at), len(duplicates_reviewed)),
    }
