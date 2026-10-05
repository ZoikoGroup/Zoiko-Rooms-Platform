"""ZR-AUTHORITY-002 orchestration -- property-scoped Authority Verification.

Routes (Sections 5-7): OWNER (Owner / Co-owner / entity representative),
AGENT (Agent / Property Manager, personally or through an organization) and
SUBLET (Tenant / Subletter). Each route's requirements come from the
Authority Regulatory Pack (Section 4); the frontend renders them.

Doctrine enforced here:
- authority is never inferred from identity, address association,
  organization membership or tenancy alone (Section 16);
- owner approval needs property-right evidence naming the verified person;
  agent approval needs a property-scoped mandate; sublet approval needs both
  a current occupation right and permission to sublet (P0 #3-#5);
- a tenant can't self-approve sublet permission; confirmations are
  authenticated (link + one-time code) and auditable (Section 13);
- conflicting claims are preserved and reviewed -- never "last upload wins"
  (Section 9.2); renewal and reconsideration create linked records;
- VERIFIED is only ever set here, by policy or a reviewer (Section 12.4);
- revocation / expiry apply the pack's listing control (Section 12.4 / P0 #9).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.events import emit_event
from app.models.admin_user import AdminUser
from app.models.authority_verification import (
    ACTIVE_STATES, CONFIRMATION_KINDS, RELATIONSHIP_TYPES, REVIEW_DECISIONS, ROUTE_FOR_RELATIONSHIP, SCOPE_CODES,
    AuthorityConfirmation, AuthorityEvidence, AuthorityRegulatoryPack, AuthorityVerification, Organization,
)
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount

RESOURCE = "authority_verification"
EDITABLE_STATES = ("COLLECTING", "ACTION_REQUIRED")
OPEN_STATES = ("COLLECTING", "SUBMITTED", "MANUAL_REVIEW", "ACTION_REQUIRED")
CONFIRMATION_TTL = timedelta(hours=72)
CONFIRMATION_MAX_ATTEMPTS = 5
CONFIRMATION_DAILY_LIMIT = 5

JURISDICTION_COUNTRY = {"England": "GB", "GB-ENG": "GB", "GB-WLS": "GB", "GB-SCT": "GB", "GB-NIR": "GB",
                        "IN": "IN", "US": "US"}

# -- safe reason codes ------------------------------------------------------------

REASONS: dict[str, tuple[str, str]] = {
    "IDENTITY_REQUIRED": ("Verify your identity first -- your property and listing drafts are saved.", "Verify identity"),
    "REQUIREMENT_MISSING": ("Add the evidence still required for your role.", "Add evidence"),
    "PROPERTY_MISMATCH": ("We could not confirm that this document refers to this property.", "Add evidence"),
    "NAME_MISMATCH": ("The person named in the evidence doesn't match your verified identity.", "Add evidence"),
    "PRINCIPAL_UNCONFIRMED": ("We could not confirm the owner or principal named in the evidence.", "Add evidence"),
    "SCOPE_INSUFFICIENT": ("We could not confirm that this document grants authority to advertise this property.", "Add evidence"),
    "SUBLET_PERMISSION_MISSING": ("Your tenancy alone may not permit subletting. Add the landlord's permission.", "Add consent"),
    "AUTHORITY_DATES_INVALID": ("The authority dates aren't current. Add a current mandate or permission.", "Add evidence"),
    "EVIDENCE_EXPIRED": ("A document has expired. Upload a current one.", "Replace evidence"),
    "EVIDENCE_UNREADABLE": ("We need to review your evidence. You can leave this page; we'll update the status here.", "View status"),
    "EVIDENCE_REUSED": ("We need to review your evidence. You can leave this page; we'll update the status here.", "View status"),
    "TAMPER_SIGNAL": ("We need to review your evidence. You can leave this page; we'll update the status here.", "View status"),
    "CONFLICTING_AUTHORITY": ("We need to review this property's authority. You can leave this page.", "View status"),
    "ORGANIZATION_UNVERIFIED": ("We need to verify the organization you act for. You can leave this page.", "View status"),
    "PROPERTY_NOT_VERIFIED": ("Your authority is ready; final approval waits for the property verification.", "Verify property"),
    "ENTITY_CHAIN": ("Company, trust or estate ownership needs an extra review. You can leave this page.", "View status"),
    "CO_OWNER_CONSENT_MISSING": ("Your co-owner's consent is needed in this country.", "Request consent"),
    "DOCUMENT_ONLY_MANDATE": ("We need to review the mandate. You can leave this page.", "View status"),
    "RECONSIDERATION": ("Your request for reconsideration is with our team.", "View status"),
    "AUTHORITY_EXPIRED": ("Your authority for this property has expired. Renew it to keep listing.", "Renew verification"),
    "AUTHORITY_REVOKED": ("Authority for this property has been withdrawn.", "Review status"),
    "CONFIRMATION_DECLINED": ("The owner or landlord declined the request.", "Review status"),
    # reviewer codes (Section 11.2)
    "REVIEW_APPROVED_DOCUMENTS": ("Your authority to list this property has been verified.", ""),
    "REVIEW_APPROVED_CONFIRMATION": ("Your authority to list this property has been verified.", ""),
    "REVIEW_MORE_EVIDENCE": ("We need another step to confirm your authority. Upload a current document that identifies the property.", "Add evidence"),
    "REVIEW_SCOPE_UNCLEAR": ("We could not confirm that this document grants authority to advertise this property.", "Add evidence"),
    "REVIEW_GRANTOR_AUTHORITY": ("Provide supporting evidence showing the grantor is authorized.", "Add evidence"),
    "REVIEW_CANNOT_ESTABLISH": ("The evidence submitted can't establish authority to list this property.", "Review reason"),
    "REVIEW_NOT_ENTITLED": ("We could not verify your authority to list this property.", "Review reason"),
    "REVOKED_BY_PRINCIPAL": ("The owner or landlord has withdrawn this authority.", "Review status"),
    "REVOKED_BY_HOST": ("You withdrew this authority.", ""),
    "REVOKED_BY_TRUST_SAFETY": ("Authority for this property has been withdrawn.", "Review status"),
    "OWNERSHIP_CHANGED": ("The property's ownership changed, so authority must be verified again.", "Renew verification"),
    "PROPERTY_ADDRESS_CHANGED": ("The property's address changed, so your authority must be verified again for the new address.", "Renew verification"),
}
REVIEW_REASONS = {
    "APPROVE": ("REVIEW_APPROVED_DOCUMENTS", "REVIEW_APPROVED_CONFIRMATION"),
    "REQUEST_EVIDENCE": ("REVIEW_MORE_EVIDENCE", "REVIEW_SCOPE_UNCLEAR", "REVIEW_GRANTOR_AUTHORITY"),
    "REJECT": ("REVIEW_CANNOT_ESTABLISH", "REVIEW_NOT_ENTITLED"),
}
REVOCATION_REASONS = ("REVOKED_BY_PRINCIPAL", "REVOKED_BY_HOST", "REVOKED_BY_TRUST_SAFETY", "OWNERSHIP_CHANGED",
                      "PROPERTY_ADDRESS_CHANGED")


def describe(codes: list[str]) -> dict:
    for code in codes or []:
        if code in REASONS:
            return {"message": REASONS[code][0], "cta": REASONS[code][1]}
    return {"message": "", "cta": ""}


# -- Authority Regulatory Packs (Section 4) ----------------------------------------

def _req(requirement_id, evidence_class, title, purpose, examples, *, required=True, owner_confirmation=False):
    return {"requirement_id": requirement_id, "evidence_class": evidence_class, "title": title, "purpose": purpose,
            "accepted_examples": examples, "required": required, "owner_confirmation": owner_confirmation}


def _routes(country: str) -> dict:
    owner_examples = {
        "GB": ["HM Land Registry title register", "Transfer deed", "Council tax account in your name (with title)"],
        "IN": ["Sale deed", "Encumbrance certificate", "Property tax receipt with mutation record", "Khata / Patta"],
        "US": ["Recorded deed", "County assessor / tax record", "Title insurance policy"],
    }.get(country, ["Title / land registry extract", "Deed", "Court, estate or trust record"])
    return {
        "OWNER": [
            _req("OWNER_PROPERTY_RIGHT", "PROPERTY_RIGHT", "Property right / ownership",
                 "Shows you own (or hold a right in) this property. An address document alone does not prove ownership.",
                 owner_examples),
            _req("OWNER_ENTITY_AUTHORITY", "ORGANIZATION_LINK", "Authority to act for the owning company / trust",
                 "Needed only when the property is owned by a company, trust or estate.",
                 ["Board resolution", "Trust deed naming you", "Letter of administration"], required=False),
            _req("CO_OWNER_CONSENT", "SUBLET_PERMISSION", "Co-owner consent",
                 "Where required, your co-owner(s) agree to the listing.", ["Signed co-owner consent"],
                 required=False, owner_confirmation=True),
        ],
        "AGENT": [
            _req("AGENT_MANDATE", "MANDATE", "Owner / principal mandate for this property",
                 "Shows the owner or authorized principal permits you to advertise and rent this property.",
                 ["Agency / letting agreement", "Property-management agreement", "Signed owner mandate", "Power of attorney"],
                 owner_confirmation=True),
            _req("AGENT_ORGANIZATION_LINK", "ORGANIZATION_LINK", "Your role at the organization",
                 "Needed only when you act through a company. A company registration is not a property mandate.",
                 ["Employment / appointment letter", "Company profile naming you"], required=False),
        ],
        "SUBLET": [
            _req("TENANT_OCCUPATION_RIGHT", "OCCUPATION_RIGHT", "Current right to occupy",
                 "Shows you currently hold a tenancy, lease or license for this property.",
                 ["Current tenancy / lease agreement", "License to occupy"]),
            _req("TENANT_SUBLET_PERMISSION", "SUBLET_PERMISSION", "Permission to sublet",
                 "Your tenancy alone may not permit subletting. Shows the landlord or authorized agent allows it.",
                 ["Landlord / agent written consent", "Lease clause permitting subletting"], owner_confirmation=True),
        ],
    }


_SEED_PACKS = (
    {"country_code": "*", "country_name": "Other countries", "sublet_consent_required": True,
     "terminology": {"tenant": "tenant", "mandate": "agency agreement"}},
    {"country_code": "GB", "country_name": "United Kingdom", "sublet_consent_required": True,
     "terminology": {"tenant": "tenant", "mandate": "letting / management agreement", "landlord": "landlord"}},
    {"country_code": "IN", "country_name": "India", "sublet_consent_required": True,
     "terminology": {"tenant": "tenant / licensee", "mandate": "authority letter / power of attorney"}},
    {"country_code": "US", "country_name": "United States", "sublet_consent_required": True,
     "co_owner_consent_required": False, "terminology": {"tenant": "tenant", "mandate": "listing / management agreement"}},
)


def ensure_default_packs(db: Session) -> None:
    existing = set(db.scalars(select(AuthorityRegulatoryPack.country_code)))
    for seed in _SEED_PACKS:
        if seed["country_code"] not in existing:
            db.add(AuthorityRegulatoryPack(version=1, active=True, requirements=_routes(seed["country_code"]), **seed))
    db.flush()


def get_pack(db: Session, country_code: str) -> AuthorityRegulatoryPack:
    ensure_default_packs(db)
    pack = db.scalar(select(AuthorityRegulatoryPack).where(
        AuthorityRegulatoryPack.country_code == (country_code or "").upper(), AuthorityRegulatoryPack.active.is_(True)))
    return pack or db.scalar(select(AuthorityRegulatoryPack).where(
        AuthorityRegulatoryPack.country_code == "*", AuthorityRegulatoryPack.active.is_(True)))


def update_pack(db: Session, pack: AuthorityRegulatoryPack, changes: dict) -> AuthorityRegulatoryPack:
    editable = ("country_name", "requirements", "terminology", "sublet_consent_required", "co_owner_consent_required",
                "parallel_identity_intake", "default_validity_days", "expiring_soon_days", "evidence_retention_days",
                "listing_control")
    unknown = set(changes) - set(editable)
    if unknown:
        raise ValueError(f"Not editable: {sorted(unknown)}")
    if "listing_control" in changes and changes["listing_control"] not in ("SUSPEND", "NONE"):
        raise ValueError("listing_control must be SUSPEND or NONE")
    data = {f: getattr(pack, f) for f in editable}
    data.update(changes)
    pack.active = False
    new = AuthorityRegulatoryPack(country_code=pack.country_code, version=pack.version + 1, active=True, **data)
    db.add(new)
    db.flush()
    return new


# -- helpers ----------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _norm(value: str | None) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (value or "").lower()).split())


def _hash(value: str) -> str:
    return hashlib.sha256(_norm(value).encode("utf-8")).hexdigest() if value else ""


def route_for(v: AuthorityVerification) -> str:
    return ROUTE_FOR_RELATIONSHIP[v.relationship_type]


def _event(db: Session, event_type: str, v: AuthorityVerification, *, reason_codes: list[str] | None = None,
           previous_state: str | None = None, new_state: str | None = None, actor: UserAccount | AdminUser | None = None,
           correlation_id: str = "", extra: dict | None = None) -> None:
    """Section 12.3: ids, states, reason codes and constrained metadata --
    never documents, identity data or reviewer notes."""
    actor_kind = "admin" if isinstance(actor, AdminUser) else "user" if actor else "system"
    emit_event(db, event_type, RESOURCE, str(v.id),
               {"authorityVerificationId": v.id, "propertyId": v.property_id, "partyId": v.party_id,
                "relationshipType": v.relationship_type, "reasonCodes": list(reason_codes or []), **(extra or {})},
               correlation_id=correlation_id, actor_kind=actor_kind, actor_id=str(actor.id) if actor else "",
               previous_state=previous_state, new_state=new_state)


def _touch(v: AuthorityVerification) -> None:
    v.version += 1
    v.updated_at = _now()


def _check_version(v: AuthorityVerification, expected: int | None) -> None:
    if expected is not None and expected != v.version:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This authority verification changed in another window. Reload to see the latest version.")


def _editable(v: AuthorityVerification) -> None:
    if v.state not in EDITABLE_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "This authority verification can't be changed now")
    if v.state == "ACTION_REQUIRED":
        v.state = "COLLECTING"


def _set_state(db: Session, v: AuthorityVerification, new_state: str) -> str:
    allowed = {
        "COLLECTING": {"SUBMITTED", "SUPERSEDED"},
        "SUBMITTED": {"VERIFIED", "MANUAL_REVIEW", "ACTION_REQUIRED"},
        "MANUAL_REVIEW": {"VERIFIED", "ACTION_REQUIRED", "REJECTED", "SUPERSEDED"},
        "ACTION_REQUIRED": {"COLLECTING", "SUBMITTED", "SUPERSEDED"},
        "VERIFIED": {"EXPIRED", "REVOKED", "SUPERSEDED"},
    }
    previous = v.state
    if new_state not in allowed.get(previous, set()):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Authority can't move from {previous} to {new_state}")
    v.state = new_state
    return previous


def effective_state(v: AuthorityVerification | None, now: datetime | None = None) -> str:
    if v is None:
        return "NOT_STARTED"
    now = now or _now()
    if v.state == "VERIFIED" and v.expires_at:
        expires = _utc(v.expires_at)
        if expires <= now:
            return "EXPIRED"
        if expires - now <= timedelta(days=30):
            return "EXPIRING_SOON"
    return v.state


def country_for(prop: Property) -> str:
    return prop.country_code or JURISDICTION_COUNTRY.get(prop.jurisdiction_code, "")


def get_owned_property(db: Session, user: UserAccount, property_id: int) -> Property:
    prop = db.get(Property, property_id)
    if prop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property not found")
    if not user.party_id or prop.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only verify authority for properties you list")
    return prop


def get_owned(db: Session, user: UserAccount, verification_id: int) -> AuthorityVerification:
    v = db.get(AuthorityVerification, verification_id)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Authority verification not found")
    if not user.party_id or v.party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own authority verifications")
    return v


def latest_for_property(db: Session, property_id: int, party_id: int) -> AuthorityVerification | None:
    return db.scalar(select(AuthorityVerification).where(
        AuthorityVerification.property_id == property_id, AuthorityVerification.party_id == party_id,
        AuthorityVerification.state != "SUPERSEDED",
    ).order_by(AuthorityVerification.id.desc()))


def valid_for_room(db: Session, room_id: int) -> AuthorityVerification | None:
    """The authority gate (Section 2.4 / P0 #10): a current VERIFIED
    assertion by the room's listing party covering the room."""
    room = db.get(Room, room_id)
    if room is None:
        return None
    now = _now()
    rows = db.scalars(select(AuthorityVerification).where(
        AuthorityVerification.property_id == room.property_id,
        AuthorityVerification.party_id == room.property.owner_party_id,
        AuthorityVerification.state == "VERIFIED",
        (AuthorityVerification.expires_at.is_(None)) | (AuthorityVerification.expires_at > now),
    ).order_by(AuthorityVerification.id.desc()))
    for v in rows:
        if not v.room_scope_ids or room_id in v.room_scope_ids:
            return v
    return None


def _identity_verified(db: Session, party_id: int) -> bool:
    from app.crud.identity_verification import get_verified_identity_for_party

    return get_verified_identity_for_party(db, party_id) is not None


def _verified_legal_name(db: Session, party_id: int) -> str:
    from app.services.identity.service import get_profile

    profile = get_profile(db, party_id)
    if profile is None:
        return ""
    return profile.verified_legal_name or profile.legal_name or ""


def _property_verified(db: Session, property_id: int) -> bool:
    from app.services.property_location_service import valid_for_property

    return valid_for_property(db, property_id) is not None


# -- requirements -----------------------------------------------------------------

def requirements(db: Session, v: AuthorityVerification) -> list[dict]:
    """Server-defined requirements for this route and jurisdiction, with each
    requirement's current status (Section 15.3)."""
    pack = get_pack(db, v.country_code)
    out = []
    for req in (pack.requirements or {}).get(route_for(v), []):
        rid = req["requirement_id"]
        required = req["required"]
        if rid == "OWNER_ENTITY_AUTHORITY":
            required = v.relationship_type == "REPRESENTATIVE"
        if rid == "CO_OWNER_CONSENT":
            required = v.relationship_type == "CO_OWNER" and pack.co_owner_consent_required
        if rid == "AGENT_ORGANIZATION_LINK":
            required = v.acting_capacity == "ORGANIZATION"
        if rid == "TENANT_SUBLET_PERMISSION":
            required = pack.sublet_consent_required
        items = [e for e in v.evidence if e.requirement_id == rid and e.processing_status != "REPLACED"]
        confirmations = [c for c in v.confirmations if c.requirement_id == rid]
        confirmed = any(c.status == "CONFIRMED" for c in confirmations)
        pending = any(c.status == "PENDING" and _utc(c.expires_at) > _now() for c in confirmations)
        if confirmed or any(e.processing_status == "READY" for e in items):
            req_status = "READY"
        elif any(e.processing_status == "NEEDS_REPLACEMENT" for e in items):
            req_status = "NEEDS_REPLACEMENT"
        elif pending:
            req_status = "AWAITING_CONFIRMATION"
        else:
            req_status = "MISSING"
        out.append({**req, "required": required, "status": req_status,
                    "evidence_ids": [e.id for e in items], "confirmation_pending": pending, "confirmed": confirmed})
    return out


def allowed_actions(db: Session, v: AuthorityVerification) -> list[str]:
    """Section 15.3: every CTA is derived from server state."""
    state = effective_state(v)
    actions = []
    if v.state in EDITABLE_STATES:
        actions += ["EDIT_DETAILS", "ADD_EVIDENCE", "REQUEST_CONFIRMATION", "SUBMIT"]
    if state in ("VERIFIED", "EXPIRING_SOON"):
        actions += ["RENEW", "REVOKE"]
    if state == "EXPIRED":
        actions.append("RENEW")
    if state == "REJECTED":
        actions.append("REQUEST_RECONSIDERATION")
    if state in ("REVOKED",):
        actions.append("START_NEW")
    return actions


# -- start / details --------------------------------------------------------------

def start(db: Session, user: UserAccount, property_id: int, *, relationship_type: str,
          room_scope_ids: list[int] | None = None, idempotency_key: str | None = None,
          correlation_id: str = "") -> AuthorityVerification:
    """Section 3: start or resume. Identity first (unless the pack allows
    parallel intake); current authority is reused; a role change creates a
    new relationship record rather than mutating the old one."""
    prop = get_owned_property(db, user, property_id)
    relationship_type = (relationship_type or "").upper()
    if relationship_type not in RELATIONSHIP_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"relationshipType must be one of {RELATIONSHIP_TYPES}")
    country = country_for(prop)
    pack = get_pack(db, country)
    if not pack.parallel_identity_intake and not _identity_verified(db, user.party_id):
        raise HTTPException(status.HTTP_409_CONFLICT, REASONS["IDENTITY_REQUIRED"][0])
    if idempotency_key:
        existing = db.scalar(select(AuthorityVerification).where(
            AuthorityVerification.party_id == user.party_id, AuthorityVerification.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    rooms = [r.id for r in prop.rooms]
    scope_rooms = [int(r) for r in (room_scope_ids or []) if int(r) in rooms]
    current = db.scalar(select(AuthorityVerification).where(
        AuthorityVerification.property_id == prop.id, AuthorityVerification.party_id == user.party_id,
        AuthorityVerification.relationship_type == relationship_type, AuthorityVerification.state.in_(ACTIVE_STATES),
    ).order_by(AuthorityVerification.id.desc()))
    if current is not None and effective_state(current) not in ("EXPIRED",):
        return current  # Section 3.2: do not re-run current authority; resume an open one
    v = AuthorityVerification(
        property_id=prop.id, party_id=user.party_id, account_user_id=user.id, relationship_type=relationship_type,
        room_scope_ids=scope_rooms, country_code=country, pack_version=pack.version, state="COLLECTING",
        idempotency_key=idempotency_key,
        scope_codes=["SUBLET", "ADVERTISE"] if relationship_type == "TENANT_SUBLETTER" else ["ADVERTISE", "RENT"],
    )
    db.add(v)
    db.flush()
    _event(db, "AUTHORITY_VERIFICATION_STARTED", v, previous_state="NOT_STARTED", new_state="COLLECTING",
           actor=user, correlation_id=correlation_id)
    _event(db, "AUTHORITY_RELATIONSHIP_SELECTED", v, actor=user, correlation_id=correlation_id,
           extra={"route": route_for(v)})
    db.commit()
    db.refresh(v)
    return v


def _parse_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _utc(value)
    try:
        return _utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Dates must be ISO 8601 (YYYY-MM-DD)")


def set_details(db: Session, user: UserAccount, v: AuthorityVerification, *, acting_capacity: str | None = None,
                organization_id: int | None = None, principal_name: str | None = None,
                scope_codes: list[str] | None = None, effective_at=None, expires_at=None,
                restrictions: str | None = None, room_scope_ids: list[int] | None = None,
                expected_version: int | None = None, correlation_id: str = "") -> AuthorityVerification:
    """Scope, dates, principal and acting capacity (Sections 5.1, 6.2, 7.2).
    Immutable once submitted except through renewal (Section 12.4)."""
    from app.core.field_encryption import encrypt_text

    _check_version(v, expected_version)
    _editable(v)
    if acting_capacity is not None:
        if acting_capacity not in ("PERSONAL", "ORGANIZATION"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "actingCapacity must be PERSONAL or ORGANIZATION")
        if route_for(v) != "AGENT" and acting_capacity == "ORGANIZATION" and v.relationship_type != "REPRESENTATIVE":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only agents and entity representatives act through an organization")
        v.acting_capacity = acting_capacity
    if organization_id is not None:
        org = db.get(Organization, organization_id)
        if org is None or org.created_by_party_id != user.party_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
        v.organization_id = org.id
        v.acting_capacity = "ORGANIZATION"
    if v.acting_capacity == "PERSONAL":
        v.organization_id = None
    if principal_name is not None:
        v.principal_name_encrypted = encrypt_text(principal_name.strip()[:200]) if principal_name.strip() else None
        v.principal_name_hash = _hash(principal_name)
    if scope_codes is not None:
        codes = [c.upper() for c in scope_codes]
        if not set(codes) <= set(SCOPE_CODES):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"scopeCodes must be among {SCOPE_CODES}")
        v.scope_codes = list(dict.fromkeys(codes))
    if effective_at is not None:
        v.effective_at = _parse_dt(effective_at)
    if expires_at is not None:
        v.expires_at = _parse_dt(expires_at)
    if v.effective_at and v.expires_at and v.expires_at <= v.effective_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The end date must be after the start date")
    if restrictions is not None:
        v.restrictions = restrictions.strip()[:1000]
    if room_scope_ids is not None:
        rooms = {r.id for r in db.get(Property, v.property_id).rooms}
        v.room_scope_ids = [int(r) for r in room_scope_ids if int(r) in rooms]
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


def principal_name(v: AuthorityVerification) -> str:
    from app.core.field_encryption import decrypt_text

    return decrypt_text(v.principal_name_encrypted) if v.principal_name_encrypted else ""


# -- organizations ----------------------------------------------------------------

def create_organization(db: Session, user: UserAccount, *, name: str, registration_number: str = "",
                        country_code: str = "", representative_role: str = "") -> Organization:
    if not user.party_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Your account has no party")
    if not name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the organization's name")
    existing = db.scalar(select(Organization).where(
        Organization.created_by_party_id == user.party_id, func.lower(Organization.name) == name.strip().lower()))
    if existing is not None:
        return existing
    org = Organization(created_by_party_id=user.party_id, name=name.strip()[:200],
                       registration_number=registration_number.strip()[:100], country_code=country_code.upper()[:10],
                       representative_role=representative_role.strip()[:100], status="PENDING")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def list_organizations(db: Session, user: UserAccount) -> list[Organization]:
    return list(db.scalars(select(Organization).where(Organization.created_by_party_id == user.party_id)
                           .order_by(Organization.id)))


def decide_organization(db: Session, admin: AdminUser, org: Organization, *, approve: bool) -> Organization:
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    org.status = "VERIFIED" if approve else "REJECTED"
    org.verified_at = _now() if approve else None
    org.verified_by_admin_id = admin.id
    db.commit()
    db.refresh(org)
    return org


# -- evidence ---------------------------------------------------------------------

def _evidence_dir() -> Path:
    path = Path(settings.authority_upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_evidence(evidence: AuthorityEvidence) -> bytes | None:
    from app.core.field_encryption import decrypt_bytes

    if not evidence.stored_filename:
        return None
    path = Path(settings.authority_upload_dir) / evidence.stored_filename
    return decrypt_bytes(path.read_bytes()) if path.is_file() else None


def _pdf_text(content: bytes, content_type: str) -> str:
    if content_type != "application/pdf":
        return ""
    try:
        from app.services import document_regex

        return document_regex.pdf_text(content) or ""
    except Exception:
        return ""


def _tamper_signal(content: bytes, content_type: str) -> bool:
    """A weak review input only (Section 11.1) -- never an accusation:
    incrementally-saved PDFs or ones produced by common online editors."""
    if content_type != "application/pdf":
        return b"Photoshop" in content[:4096] or b"GIMP" in content[:4096]
    head = content[:200_000]
    editors = (b"ilovepdf", b"Smallpdf", b"PDFescape", b"Sejda", b"PDF-XChange Editor", b"Photoshop")
    return content.count(b"%%EOF") > 1 or any(e.lower() in head.lower() for e in editors)


def _property_in_text(text: str, prop: Property) -> bool:
    body = _norm(text)
    line1 = _norm(prop.address_line_1 or prop.address)
    tokens = [t for t in line1.split() if len(t) > 1 or t.isdigit()]
    if not body or not tokens:
        return False
    hits = sum(1 for t in tokens if t in body.split())
    postal = (prop.postal_code or "").replace(" ", "").lower()
    return hits / len(tokens) >= 0.75 and (not postal or postal in body.replace(" ", ""))


def _name_in_text(text: str, name: str) -> bool:
    parts = [p for p in _norm(name).split() if len(p) > 1]
    body = _norm(text).split()
    return bool(parts) and all(p in body for p in parts)


def add_evidence(db: Session, user: UserAccount, v: AuthorityVerification, *, requirement_id: str, content: bytes,
                 original_filename: str, issuer: str = "", document_reference: str = "", issued_at=None,
                 expires_at=None, replaces_id: int | None = None, expected_version: int | None = None,
                 correlation_id: str = "") -> AuthorityEvidence:
    """Section 8.2 upload component: multiple items per requirement,
    replace / remove, encrypted at rest, reuse and tamper signals."""
    from app.core.field_encryption import encrypt_bytes
    from app.core.identity_uploads import _sniff, _strip_image_metadata

    _check_version(v, expected_version)
    _editable(v)
    reqs = {r["requirement_id"]: r for r in requirements(db, v)}
    if requirement_id not in reqs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This requirement doesn't apply to your role in this country")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty")
    if len(content) > settings.property_verification_document_max_size_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"File exceeds the {settings.property_verification_document_max_size_mb}MB limit")
    sniffed = _sniff(content)
    if not sniffed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported file -- upload a PDF, JPG or PNG")
    extension, content_type = sniffed
    tamper = _tamper_signal(content, content_type)
    content = _strip_image_metadata(content, extension)
    if sum(1 for e in v.evidence if e.processing_status != "REPLACED") >= 15:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can add up to 15 documents")
    replaced = None
    if replaces_id is not None:
        replaced = db.get(AuthorityEvidence, replaces_id)
        if replaced is None or replaced.verification_id != v.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document to replace not found")

    file_hash = hashlib.sha256(content).hexdigest()
    stored = f"{uuid.uuid4().hex}{extension}.enc"
    (_evidence_dir() / stored).write_bytes(encrypt_bytes(content))
    reused = db.scalar(select(func.count(AuthorityEvidence.id)).join(AuthorityVerification).where(
        AuthorityEvidence.file_hash == file_hash,
        (AuthorityVerification.property_id != v.property_id) | (AuthorityVerification.party_id != v.party_id),
    )) or 0
    text = _pdf_text(content, content_type)
    prop = db.get(Property, v.property_id)
    readable = bool(text.strip())
    my_name = _verified_legal_name(db, v.party_id)
    principal = principal_name(v)
    evidence = AuthorityEvidence(
        verification_id=v.id, requirement_id=requirement_id, evidence_type=reqs[requirement_id]["evidence_class"],
        source_type="UPLOAD", issuer=issuer.strip()[:200], document_reference=document_reference.strip()[:200],
        issued_at=_parse_dt(issued_at), expires_at=_parse_dt(expires_at), stored_filename=stored,
        original_filename=Path(original_filename or "document").name[:255], content_type=content_type,
        file_size=len(content), file_hash=file_hash, readable=readable,
        property_matched=_property_in_text(text, prop) if readable else None,
        name_matched=_name_in_text(text, my_name) if readable and my_name else None,
        principal_matched=_name_in_text(text, principal) if readable and principal else None,
        reused_elsewhere=reused > 0, tamper_signal=tamper, processing_status="READY",
    )
    db.add(evidence)
    db.flush()
    if replaced is not None:
        replaced.processing_status = "REPLACED"
        replaced.replaced_by_id = evidence.id
        _event(db, "AUTHORITY_EVIDENCE_REPLACED", v, actor=user, correlation_id=correlation_id,
               extra={"evidenceId": evidence.id, "replacedId": replaced.id, "requirementId": requirement_id})
    else:
        _event(db, "AUTHORITY_EVIDENCE_UPLOADED", v, actor=user, correlation_id=correlation_id,
               extra={"evidenceId": evidence.id, "requirementId": requirement_id, "evidenceType": evidence.evidence_type})
    _touch(v)
    db.commit()
    db.refresh(evidence)
    return evidence


def remove_evidence(db: Session, user: UserAccount, v: AuthorityVerification, evidence_id: int, *,
                    expected_version: int | None = None) -> None:
    _check_version(v, expected_version)
    _editable(v)
    evidence = db.get(AuthorityEvidence, evidence_id)
    if evidence is None or evidence.verification_id != v.id or evidence.source_type != "UPLOAD":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if evidence.stored_filename:
        (Path(settings.authority_upload_dir) / evidence.stored_filename).unlink(missing_ok=True)
    db.delete(evidence)
    _touch(v)
    db.commit()


# -- owner / landlord confirmation (Sections 6.2 step 7, 7.3, 13) ------------------

def request_confirmation(db: Session, user: UserAccount, v: AuthorityVerification, *, requirement_id: str,
                         recipient_email: str, recipient_name: str = "", idempotency_key: str | None = None,
                         expected_version: int | None = None, correlation_id: str = "") -> AuthorityConfirmation:
    from app.core.field_encryption import encrypt_text

    _check_version(v, expected_version)
    _editable(v)
    reqs = {r["requirement_id"]: r for r in requirements(db, v)}
    req = reqs.get(requirement_id)
    if req is None or not req.get("owner_confirmation"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A confirmation request isn't available for this requirement")
    email = recipient_email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the owner's or landlord's email address")
    if idempotency_key:
        existing = db.scalar(select(AuthorityConfirmation).where(
            AuthorityConfirmation.verification_id == v.id, AuthorityConfirmation.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    # Section 13.1: a tenant / agent can't approve their own authority.
    own_emails = {u.email.lower() for u in db.scalars(select(UserAccount).where(UserAccount.party_id == v.party_id))}
    if email in own_emails:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't confirm your own authority -- enter the owner's or landlord's email")
    recent = db.scalar(select(func.count(AuthorityConfirmation.id)).where(
        AuthorityConfirmation.verification_id == v.id,
        AuthorityConfirmation.created_at >= _now() - timedelta(days=1))) or 0
    if recent >= CONFIRMATION_DAILY_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many confirmation requests today -- try again tomorrow")
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1_000_000):06d}"
    kind = {"AGENT_MANDATE": "MANDATE", "TENANT_SUBLET_PERMISSION": "SUBLET_PERMISSION",
            "CO_OWNER_CONSENT": "CO_OWNER_CONSENT"}.get(requirement_id, "MANDATE")
    confirmation = AuthorityConfirmation(
        verification_id=v.id, kind=kind, requirement_id=requirement_id,
        recipient_email_encrypted=encrypt_text(email), recipient_email_hash=hashlib.sha256(email.encode()).hexdigest(),
        recipient_name=recipient_name.strip()[:200], token_hash=hashlib.sha256(token.encode()).hexdigest(),
        code_hash=hashlib.sha256(code.encode()).hexdigest(), expires_at=_now() + CONFIRMATION_TTL,
        idempotency_key=idempotency_key,
    )
    db.add(confirmation)
    db.flush()
    _event(db, "OWNER_CONFIRMATION_REQUESTED", v, actor=user, correlation_id=correlation_id,
           extra={"confirmationId": confirmation.id, "kind": kind})
    _touch(v)
    db.commit()
    db.refresh(confirmation)
    _send_confirmation_emails(db, v, confirmation, email, token, code)
    return confirmation


def _send_confirmation_emails(db: Session, v: AuthorityVerification, c: AuthorityConfirmation, email: str,
                              token: str, code: str) -> None:
    from app.core.mailer import send_email

    prop = db.get(Property, v.property_id)
    requester = _verified_legal_name(db, v.party_id) or "A Zoiko Rooms host"
    what = {"MANDATE": "advertise and rent your property as your agent / property manager",
            "SUBLET_PERMISSION": "sublet the property they rent from you",
            "CO_OWNER_CONSENT": "list the property you co-own"}[c.kind]
    link = f"{settings.frontend_url.rstrip('/')}/account/authority-confirmation?token={token}"
    send_email(email, "Please confirm a request on Zoiko Rooms", heading="Confirm or decline a request",
               body_lines=[f"{requester} asks for your permission to {what} ({prop.city}).",
                           "Open the secure link and enter the 6-digit code we send in a separate email.",
                           "The link works for 72 hours. If you don't recognize this request, decline it."],
               cta_label="Review the request", cta_url=link)
    send_email(email, "Your Zoiko Rooms confirmation code", heading="Your confirmation code",
               body_lines=[f"Your code is {code}.", "Enter it on the confirmation page. Never share it with anyone."])


def _find_confirmation(db: Session, token: str) -> AuthorityConfirmation:
    c = db.scalar(select(AuthorityConfirmation).where(
        AuthorityConfirmation.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link isn't valid")
    if c.status == "PENDING" and _utc(c.expires_at) <= _now():
        c.status = "EXPIRED"
        db.commit()
    return c


def confirmation_summary(db: Session, token: str) -> dict:
    """What the owner / landlord sees before confirming: no documents, no
    personal data beyond the requester's name and the property's city."""
    c = _find_confirmation(db, token)
    v = c.verification
    prop = db.get(Property, v.property_id)
    return {
        "status": c.status, "kind": c.kind, "expiresAt": c.expires_at,
        "requesterName": _verified_legal_name(db, v.party_id) or "A Zoiko Rooms host",
        "propertyCity": prop.city, "propertyCountry": v.country_code, "relationshipType": v.relationship_type,
        "scopeCodes": list(v.scope_codes or []), "effectiveAt": v.effective_at, "endsAt": v.expires_at,
        "restrictions": v.restrictions,
    }


def respond_to_confirmation(db: Session, token: str, *, code: str, decision: str, responder_name: str,
                            client_ip: str = "", user_agent: str = "") -> dict:
    """Authenticated by link + one-time code; attempts are limited; the
    response is recorded with hashed network metadata (Section 13)."""
    c = _find_confirmation(db, token)
    if c.status != "PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This request is already {c.status.lower()}")
    if c.attempts >= CONFIRMATION_MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts -- ask the host to send a new request")
    c.attempts += 1
    if not secrets.compare_digest(hashlib.sha256(code.strip().encode()).hexdigest(), c.code_hash):
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code isn't right")
    decision = decision.upper()
    if decision not in ("CONFIRM", "DECLINE"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "decision must be CONFIRM or DECLINE")
    if decision == "CONFIRM" and not responder_name.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter your full name to confirm")
    v = c.verification
    c.status = "CONFIRMED" if decision == "CONFIRM" else "DECLINED"
    c.responded_at = _now()
    c.responder_name = responder_name.strip()[:200]
    c.responder_ip_hash = hashlib.sha256(client_ip.encode()).hexdigest() if client_ip else ""
    c.responder_agent = user_agent[:200]
    if c.status == "CONFIRMED":
        db.add(AuthorityEvidence(
            verification_id=v.id, requirement_id=c.requirement_id,
            evidence_type="SUBLET_PERMISSION" if c.kind != "MANDATE" else "MANDATE", source_type="OWNER_CONFIRMATION",
            issuer=c.responder_name, document_reference=f"confirmation:{c.id}", processing_status="READY",
            readable=True, property_matched=True, principal_matched=bool(v.principal_name_hash) and
            _hash(c.responder_name) == v.principal_name_hash or None, confirmation_id=c.id,
        ))
    elif v.state == "VERIFIED":
        # A principal who declines now withdraws existing authority.
        _revoke(db, v, reason_code="REVOKED_BY_PRINCIPAL", actor=None)
    _event(db, "OWNER_CONFIRMATION_COMPLETED", v, extra={"confirmationId": c.id, "kind": c.kind, "outcome": c.status},
           reason_codes=["CONFIRMATION_DECLINED"] if c.status == "DECLINED" else [])
    _touch(v)
    db.commit()
    from app.crud import notification as notif_crud

    notif_crud.notify_user_by_party(
        db, v.party_id, title="Owner / landlord responded",
        message="Your request was confirmed." if c.status == "CONFIRMED" else REASONS["CONFIRMATION_DECLINED"][0],
        notification_type="authority.confirmation", related_entity_type=RESOURCE, related_entity_id=str(v.id),
    )
    db.commit()
    return {"status": c.status}


# -- submit & automated decision (Sections 4.3, 11.1) ------------------------------

def _match_model(db: Session, v: AuthorityVerification) -> dict:
    live = [e for e in v.evidence if e.processing_status == "READY"]
    route = route_for(v)
    primary_class = {"OWNER": "PROPERTY_RIGHT", "AGENT": "MANDATE", "SUBLET": "OCCUPATION_RIGHT"}[route]
    primary = [e for e in live if e.evidence_type == primary_class]
    confirmed = [e for e in live if e.source_type == "OWNER_CONFIRMATION"]

    def tri(values):  # True if any True, False if all False, None if unknown
        known = [x for x in values if x is not None]
        return True if any(known) else (False if known else None)

    property_match = tri([e.property_matched for e in primary] + ([True] if confirmed else []))
    if route == "OWNER":
        representative = tri([e.name_matched for e in primary])
        principal = representative  # the owner is the principal
    else:
        representative = tri([e.name_matched for e in primary]) if primary else (True if confirmed else None)
        principal = tri([e.principal_matched for e in live if e.evidence_type in ("MANDATE", "SUBLET_PERMISSION")])
    needed_scope = {"OWNER": {"ADVERTISE"}, "AGENT": {"ADVERTISE"}, "SUBLET": {"SUBLET"}}[route]
    scope = needed_scope <= set(v.scope_codes or [])
    now = _now()
    validity = not (v.expires_at and _utc(v.expires_at) <= now) and not (v.effective_at and _utc(v.effective_at) > now + timedelta(days=1))
    evidence_expired = any(e.expires_at and _utc(e.expires_at) <= now for e in live)
    integrity = not any(e.reused_elsewhere or e.tamper_signal for e in live)
    return {"property_match": property_match, "representative_match": representative, "principal_match": principal,
            "scope_match": scope, "validity_result": validity and not evidence_expired,
            "source_integrity": integrity, "owner_confirmation": bool(confirmed),
            "unreadable_documents": any(not e.readable for e in live if e.source_type == "UPLOAD")}


def _conflicts(db: Session, v: AuthorityVerification) -> bool:
    """Another party claims authority over the same property (Section 9.2)."""
    return (db.scalar(select(func.count(AuthorityVerification.id)).where(
        AuthorityVerification.property_id == v.property_id, AuthorityVerification.party_id != v.party_id,
        AuthorityVerification.state.in_(("SUBMITTED", "MANUAL_REVIEW", "VERIFIED")))) or 0) > 0


def decide(db: Session, v: AuthorityVerification) -> tuple[str, str, list[str]]:
    """-> (state, assurance level, reason codes). Automation approves only a
    complete, matching, current, uncontested chain; it escalates anything it
    can't safely decide and asks for evidence where a fix is clear."""
    pack = get_pack(db, v.country_code)
    m = _match_model(db, v)
    v.match_results = m
    route = route_for(v)
    action, review = [], []
    reqs = requirements(db, v)
    if any(r["required"] and r["status"] != "READY" for r in reqs):
        action.append("SUBLET_PERMISSION_MISSING" if route == "SUBLET" and any(
            r["requirement_id"] == "TENANT_SUBLET_PERMISSION" and r["status"] != "READY" for r in reqs) else
            "CO_OWNER_CONSENT_MISSING" if any(r["requirement_id"] == "CO_OWNER_CONSENT" and r["status"] != "READY"
                                             and r["required"] for r in reqs) else "REQUIREMENT_MISSING")
    if not m["scope_match"]:
        action.append("SCOPE_INSUFFICIENT")
    if not m["validity_result"]:
        action.append("AUTHORITY_DATES_INVALID")
    if m["property_match"] is False:
        action.append("PROPERTY_MISMATCH")
    if m["representative_match"] is False:
        action.append("NAME_MISMATCH")
    if m["unreadable_documents"] or m["property_match"] is None or m["representative_match"] is None:
        review.append("EVIDENCE_UNREADABLE")
    if not m["source_integrity"]:
        review.append("EVIDENCE_REUSED" if any(e.reused_elsewhere for e in v.evidence) else "TAMPER_SIGNAL")
    if _conflicts(db, v):
        review.append("CONFLICTING_AUTHORITY")
    if v.acting_capacity == "ORGANIZATION" and (v.organization is None or v.organization.status != "VERIFIED"):
        review.append("ORGANIZATION_UNVERIFIED")
    if v.relationship_type == "REPRESENTATIVE":
        review.append("ENTITY_CHAIN")
    if route == "AGENT" and not m["owner_confirmation"] and m["principal_match"] is not True:
        review.append("DOCUMENT_ONLY_MANDATE")
    if route == "SUBLET" and pack.sublet_consent_required and not m["owner_confirmation"]:
        # A tenant can't self-evidence permission: a consent document or lease
        # clause is checked by a person unless the landlord confirmed it.
        review.append("PRINCIPAL_UNCONFIRMED")
    if not _property_verified(db, v.property_id):
        review.append("PROPERTY_NOT_VERIFIED")
    if v.is_reconsideration:
        review.append("RECONSIDERATION")
    if action:
        return "ACTION_REQUIRED", "AV-X", list(dict.fromkeys(action + review))
    if review:
        return "MANUAL_REVIEW", "AV-0", list(dict.fromkeys(review))
    return "VERIFIED", "AV-1", []


def submit(db: Session, user: UserAccount, v: AuthorityVerification, *, attested: bool,
           idempotency_key: str | None = None, expected_version: int | None = None,
           correlation_id: str = "") -> AuthorityVerification:
    if idempotency_key and v.submit_idempotency_key == idempotency_key:
        return v
    _check_version(v, expected_version)
    if not attested:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Confirm the evidence is current, has not been revoked and you are authorized to submit it")
    _editable(v)
    if not any(e.processing_status == "READY" for e in v.evidence):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, REASONS["REQUIREMENT_MISSING"][0])
    _set_state(db, v, "SUBMITTED")
    now = _now()
    v.attested_at = v.submitted_at = now
    v.submit_idempotency_key = idempotency_key
    state, level, codes = decide(db, v)
    _event(db, "AUTHORITY_AUTOMATED_CHECK_COMPLETED", v, actor=user, correlation_id=correlation_id,
           reason_codes=codes, extra={"outcome": state, "matchResults": {k: v.match_results.get(k) for k in (
               "property_match", "representative_match", "principal_match", "scope_match", "validity_result")}})
    v.reason_codes = codes
    if state == "VERIFIED":
        _approve(db, v, assurance=level, actor=user, correlation_id=correlation_id)
    elif state == "MANUAL_REVIEW":
        _set_state(db, v, "MANUAL_REVIEW")
        _event(db, "AUTHORITY_MANUAL_REVIEW_STARTED", v, actor=user, reason_codes=codes, previous_state="SUBMITTED",
               new_state="MANUAL_REVIEW", correlation_id=correlation_id)
        _notify(db, v, "Authority verification in review", REASONS.get(codes[0], ("We're reviewing your authority.",))[0])
        _notify_reviewers(db, v)
    else:
        _set_state(db, v, "ACTION_REQUIRED")
        v.assurance_level = "AV-X"
        v.decided_at = now
        _event(db, "AUTHORITY_ACTION_REQUIRED", v, actor=user, reason_codes=codes, previous_state="SUBMITTED",
               new_state="ACTION_REQUIRED", correlation_id=correlation_id)
        _notify(db, v, "Authority action required", describe(codes)["message"])
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


def _approve(db: Session, v: AuthorityVerification, *, assurance: str, actor, correlation_id: str = "") -> None:
    pack = get_pack(db, v.country_code)
    previous = v.state
    _set_state(db, v, "VERIFIED")
    now = _now()
    v.assurance_level = assurance
    v.decided_at = v.verified_at = v.last_reviewed_at = now
    v.reason_codes = []
    evidence_expiry = [_utc(e.expires_at) for e in v.evidence if e.expires_at and e.processing_status == "READY"]
    candidates = [d for d in [_utc(v.expires_at), *evidence_expiry] if d]
    v.expires_at = min(candidates) if candidates else now + timedelta(days=pack.default_validity_days)
    if v.organization and v.organization.status != "VERIFIED" and isinstance(actor, AdminUser):
        v.organization.status, v.organization.verified_at, v.organization.verified_by_admin_id = "VERIFIED", now, actor.id
    _event(db, "AUTHORITY_RENEWED" if v.previous_id else "AUTHORITY_APPROVED", v, actor=actor, previous_state=previous,
           new_state="VERIFIED", correlation_id=correlation_id, extra={"assuranceLevel": assurance})
    # Section 9: the newly approved assertion becomes controlling.
    for old in db.scalars(select(AuthorityVerification).where(
            AuthorityVerification.property_id == v.property_id, AuthorityVerification.party_id == v.party_id,
            AuthorityVerification.id != v.id, AuthorityVerification.state.in_(ACTIVE_STATES))):
        prev = old.state
        old.state = "SUPERSEDED"
        old.superseded_by_id = v.id
        _touch(old)
        _event(db, "AUTHORITY_SUPERSEDED", old, previous_state=prev, new_state="SUPERSEDED", correlation_id=correlation_id,
               extra={"supersededBy": v.id})
    _notify(db, v, "Listing authority verified", "Your authority to list this property has been verified.")


def _notify(db: Session, v: AuthorityVerification, title: str, message: str) -> None:
    from app.crud import notification as notif_crud

    notif_crud.notify_user_by_party(db, v.party_id, title=title, message=message,
                                    notification_type="authority.status", related_entity_type=RESOURCE,
                                    related_entity_id=str(v.id))


def _notify_reviewers(db: Session, v: AuthorityVerification) -> None:
    from app.crud import notification as notif_crud

    notif_crud.notify_all_super_admins(
        db, title="Authority verification needs review",
        message=f"Authority verification #{v.id} (property #{v.property_id}, {v.relationship_type}) needs a reviewer.",
        notification_type="authority.review", related_entity_type=RESOURCE, related_entity_id=str(v.id),
    )


# -- review / revoke / renew / reconsider (Sections 9.2, 11) ------------------------

def review(db: Session, admin: AdminUser, v: AuthorityVerification, *, decision: str, reason_code: str,
           note: str = "", expected_version: int | None = None, correlation_id: str = "") -> AuthorityVerification:
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    _check_version(v, expected_version)
    decision = decision.upper()
    if decision not in REVIEW_DECISIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"decision must be one of {REVIEW_DECISIONS}")
    if reason_code not in REVIEW_REASONS[decision]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reasonCode must be one of {REVIEW_REASONS[decision]}")
    if v.state != "MANUAL_REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This authority verification is {v.state} and can't be reviewed")
    v.reviewer_admin_id = admin.id
    v.review_note = note.strip()[:1000]
    v.review_reason_codes = list(dict.fromkeys([*(v.review_reason_codes or []), reason_code]))
    v.last_reviewed_at = _now()
    if decision == "APPROVE":
        # Two-person review for conflicting claims and entity chains (Section 11.2).
        needs_two = any(c in (v.reason_codes or []) for c in ("CONFLICTING_AUTHORITY", "ENTITY_CHAIN"))
        if needs_two and v.first_approver_admin_id is None:
            v.first_approver_admin_id = admin.id
            _event(db, "AUTHORITY_REVIEW_FIRST_APPROVAL", v, actor=admin, reason_codes=[reason_code],
                   correlation_id=correlation_id)
            _touch(v)
            db.commit()
            db.refresh(v)
            return v
        if v.first_approver_admin_id is not None and v.first_approver_admin_id == admin.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "A second, different reviewer must approve this authority")
        _approve(db, v, assurance="AV-2", actor=admin, correlation_id=correlation_id)
    elif decision == "REJECT":
        _set_state(db, v, "REJECTED")
        v.decided_at = _now()
        v.reason_codes = [reason_code]
        _event(db, "AUTHORITY_REJECTED", v, actor=admin, reason_codes=[reason_code], previous_state="MANUAL_REVIEW",
               new_state="REJECTED", correlation_id=correlation_id)
        _notify(db, v, "Could not verify authority", describe([reason_code])["message"])
    else:
        _set_state(db, v, "ACTION_REQUIRED")
        v.assurance_level = "AV-X"
        v.decided_at = _now()
        v.reason_codes = [reason_code]
        _event(db, "AUTHORITY_ACTION_REQUIRED", v, actor=admin, reason_codes=[reason_code], previous_state="MANUAL_REVIEW",
               new_state="ACTION_REQUIRED", correlation_id=correlation_id)
        _notify(db, v, "Authority action required", describe([reason_code])["message"])
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


def _listing_control(db: Session, v: AuthorityVerification, reason: str) -> int:
    """Section 12.4 / P0 #9: expired or revoked authority suspends live
    listings at the property when the pack says so."""
    from app.crud.listing import suspend_listing
    from app.models.listing import Listing

    pack = get_pack(db, v.country_code)
    if pack.listing_control != "SUSPEND":
        return 0
    room_ids = v.room_scope_ids or [r.id for r in db.get(Property, v.property_id).rooms]
    count = 0
    for listing in db.scalars(select(Listing).where(Listing.room_id.in_(room_ids), Listing.state == "PUBLISHED")):
        if valid_for_room(db, listing.room_id) is None:
            suspend_listing(db, listing, reason)
            count += 1
    return count


def _revoke(db: Session, v: AuthorityVerification, *, reason_code: str, actor, correlation_id: str = "") -> None:
    previous = v.state
    _set_state(db, v, "REVOKED")
    v.revoked_at = _now()
    v.revocation_reason_code = reason_code
    v.reason_codes = [reason_code]
    _event(db, "AUTHORITY_REVOKED", v, actor=actor, reason_codes=[reason_code], previous_state=previous,
           new_state="REVOKED", correlation_id=correlation_id)
    db.flush()
    suspended = _listing_control(db, v, "Listing authority withdrawn")
    _notify(db, v, "Authority withdrawn", describe([reason_code])["message"] +
            (f" {suspended} listing(s) paused." if suspended else ""))


def revoke(db: Session, actor: UserAccount | AdminUser, v: AuthorityVerification, *, reason_code: str,
           idempotency_key: str | None = None, correlation_id: str = "") -> AuthorityVerification:
    """Authorized, idempotent, audited revocation (Section 9.2). Hosts may
    withdraw their own authority; Trust & Safety may revoke any."""
    if v.state == "REVOKED":
        return v  # idempotent
    if reason_code not in REVOCATION_REASONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reasonCode must be one of {REVOCATION_REASONS}")
    if isinstance(actor, AdminUser):
        if actor.role != "super_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    elif reason_code != "REVOKED_BY_HOST":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only withdraw your own authority")
    if v.state != "VERIFIED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only verified authority can be revoked")
    _revoke(db, v, reason_code=reason_code, actor=actor, correlation_id=correlation_id)
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


def renew(db: Session, user: UserAccount, v: AuthorityVerification, *, reconsideration_note: str | None = None,
          correlation_id: str = "") -> AuthorityVerification:
    """Renewal / reconsideration: a new evidence cycle linked to the prior
    assertion; history is never overwritten (Sections 9.2, 11.3)."""
    state = effective_state(v)
    reconsider = reconsideration_note is not None
    if reconsider and state != "REJECTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a rejected verification can be reconsidered")
    if not reconsider and state not in ("VERIFIED", "EXPIRING_SOON", "EXPIRED", "REVOKED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This authority can't be renewed now")
    new = AuthorityVerification(
        property_id=v.property_id, party_id=v.party_id, account_user_id=user.id, organization_id=v.organization_id,
        relationship_type=v.relationship_type, acting_capacity=v.acting_capacity, room_scope_ids=list(v.room_scope_ids or []),
        country_code=v.country_code, pack_version=get_pack(db, v.country_code).version, state="COLLECTING",
        principal_name_encrypted=v.principal_name_encrypted, principal_name_hash=v.principal_name_hash,
        scope_codes=list(v.scope_codes or []), restrictions=v.restrictions, previous_id=v.id,
        is_reconsideration=reconsider, reconsideration_note=(reconsideration_note or "").strip()[:1000],
    )
    db.add(new)
    db.flush()
    _event(db, "AUTHORITY_VERIFICATION_STARTED", new, actor=user, previous_state="NOT_STARTED", new_state="COLLECTING",
           correlation_id=correlation_id, extra={"previousId": v.id, "reconsideration": reconsider})
    db.commit()
    db.refresh(new)
    return new


# -- jobs --------------------------------------------------------------------------

def reopen_for_address_change(db: Session, property_id: int, *, correlation_id: str = "") -> int:
    """ZR-PROPERTY-VERIFY-001 Section 13.3: a material address change reopens
    authority -- verified authority for the old address doesn't silently
    transfer. Verified records are revoked (renewable, history kept); open
    ones go back to the host to re-check."""
    count = 0
    for v in db.scalars(select(AuthorityVerification).where(
            AuthorityVerification.property_id == property_id, AuthorityVerification.state.in_(ACTIVE_STATES))):
        if v.state == "VERIFIED":
            _revoke(db, v, reason_code="PROPERTY_ADDRESS_CHANGED", actor=None, correlation_id=correlation_id)
        elif v.state in ("SUBMITTED", "MANUAL_REVIEW"):
            previous = v.state
            v.state = "ACTION_REQUIRED"
            v.reason_codes = ["PROPERTY_ADDRESS_CHANGED"]
            _event(db, "AUTHORITY_ACTION_REQUIRED", v, reason_codes=["PROPERTY_ADDRESS_CHANGED"],
                   previous_state=previous, new_state="ACTION_REQUIRED", correlation_id=correlation_id)
        else:
            continue
        _touch(v)
        count += 1
    return count


def sweep_expiry(db: Session) -> dict:
    """EXPIRING_SOON notices once; EXPIRED with listing control (Section 9)."""
    now = _now()
    expiring = expired = 0
    for v in db.scalars(select(AuthorityVerification).where(AuthorityVerification.state == "VERIFIED",
                                                            AuthorityVerification.expires_at.is_not(None))):
        pack = get_pack(db, v.country_code)
        expires = _utc(v.expires_at)
        if expires <= now:
            v.state = "EXPIRED"
            v.reason_codes = ["AUTHORITY_EXPIRED"]
            _touch(v)
            _event(db, "AUTHORITY_EXPIRED", v, reason_codes=["AUTHORITY_EXPIRED"], previous_state="VERIFIED",
                   new_state="EXPIRED")
            db.flush()
            _listing_control(db, v, "Listing authority expired")
            _notify(db, v, "Authority expired", REASONS["AUTHORITY_EXPIRED"][0])
            expired += 1
        elif expires - now <= timedelta(days=pack.expiring_soon_days) and v.expiring_notified_at is None:
            v.expiring_notified_at = now
            _event(db, "AUTHORITY_EXPIRING", v, extra={"expiresAt": expires.isoformat()})
            _notify(db, v, "Authority expiring soon", f"Your authority for this property expires on {expires:%d %b %Y}. Renew it to keep listing.")
            expiring += 1
    db.commit()
    return {"expiring": expiring, "expired": expired}


def purge_expired_evidence(db: Session) -> int:
    purged = 0
    for e in db.scalars(select(AuthorityEvidence).where(AuthorityEvidence.purged_at.is_(None),
                                                         AuthorityEvidence.stored_filename.is_not(None))):
        v = e.verification
        pack = get_pack(db, v.country_code)
        decided = _utc(v.decided_at)
        if not pack.evidence_retention_days or decided is None or v.state in OPEN_STATES:
            continue
        if decided + timedelta(days=pack.evidence_retention_days) <= _now():
            (Path(settings.authority_upload_dir) / e.stored_filename).unlink(missing_ok=True)
            e.stored_filename, e.purged_at = None, _now()
            purged += 1
    db.commit()
    return purged


# -- publication eligibility (Section 12.2) ----------------------------------------

def publication_eligibility(db: Session, prop: Property) -> dict:
    """Evaluated gates per room; the publish endpoint re-checks independently."""
    identity = _identity_verified(db, prop.owner_party_id)
    property_ok = _property_verified(db, prop.id)
    rooms = []
    for room in prop.rooms:
        authority = valid_for_room(db, room.id)
        rooms.append({"roomId": room.id, "authority": authority is not None,
                      "authorityExpiresAt": authority.expires_at if authority else None})
    reasons = []
    if not identity:
        reasons.append("IDENTITY_NOT_VERIFIED")
    if not property_ok:
        reasons.append("PROPERTY_NOT_VERIFIED")
    if not any(r["authority"] for r in rooms):
        reasons.append("AUTHORITY_NOT_VERIFIED")
    return {"propertyId": prop.id, "identityVerified": identity, "propertyVerified": property_ok, "rooms": rooms,
            "reasonCodes": reasons, "eligible": not reasons}


def metrics(db: Session, *, days: int = 30) -> dict:
    since = _now() - timedelta(days=days)
    rows = list(db.scalars(select(AuthorityVerification).where(AuthorityVerification.created_at >= since)))
    submitted = [v for v in rows if v.submitted_at]

    def rate(a, b):
        return round(100 * a / b, 1) if b else None

    by_route: dict[str, dict] = {}
    for v in rows:
        r = by_route.setdefault(route_for(v), {"started": 0, "verified": 0})
        r["started"] += 1
        r["verified"] += int(v.verified_at is not None)
    reasons: dict[str, int] = {}
    for v in rows:
        for code in v.reason_codes or []:
            reasons[code] = reasons.get(code, 0) + 1
    return {
        "period_days": days, "started": len(rows), "submitted": len(submitted),
        "verified": sum(1 for v in rows if v.verified_at),
        "auto_approval_rate": rate(sum(1 for v in submitted if v.assurance_level == "AV-1"), len(submitted)),
        "manual_review_rate": rate(sum(1 for v in submitted if v.reviewer_admin_id or v.state == "MANUAL_REVIEW"), len(submitted)),
        "pending_review": db.scalar(select(func.count(AuthorityVerification.id)).where(
            AuthorityVerification.state == "MANUAL_REVIEW")) or 0,
        "confirmations_sent": db.scalar(select(func.count(AuthorityConfirmation.id)).where(
            AuthorityConfirmation.created_at >= since)) or 0,
        "revoked": sum(1 for v in rows if v.state == "REVOKED"),
        "by_route": by_route, "reason_codes": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
    }


_ = CONFIRMATION_KINDS  # exported for routes / docs


def room_status(db: Session, room_id: int) -> str | None:
    """Verification Center vocabulary for the room's latest authority
    assertion (None when the listing party never started one)."""
    room = db.get(Room, room_id)
    if room is None:
        return None
    rows = db.scalars(select(AuthorityVerification).where(
        AuthorityVerification.property_id == room.property_id,
        AuthorityVerification.party_id == room.property.owner_party_id,
        AuthorityVerification.state != "SUPERSEDED",
    ).order_by(AuthorityVerification.id.desc()))
    v = next((r for r in rows if not r.room_scope_ids or room_id in r.room_scope_ids), None)
    if v is None:
        return None
    return {"COLLECTING": "in_progress", "SUBMITTED": "in_review", "MANUAL_REVIEW": "in_review",
            "EXPIRING_SOON": "expiring"}.get(effective_state(v), effective_state(v).lower())
