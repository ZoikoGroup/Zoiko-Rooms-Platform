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
- conflicting claims are preserved -- never "last upload wins" (Section
  9.2); renewal and reconsideration create linked records;
- every case is decided automatically from the documents (text layer or
  OCR): VERIFIED, ACTION_REQUIRED (the host is shown why, corrects it and
  submits again) or SUBMITTED while waiting on something outside the host's
  evidence (property verification, OCR engine, malware scan). There is no
  manual review queue and nothing is finally rejected; VERIFIED is only
  ever set here (Section 12.4);
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
from sqlalchemy.orm import Session, object_session

from app.core.config import settings
from app.crud.events import emit_event
from app.models.admin_user import AdminUser
from app.models.authority_verification import (
    ACTIVE_STATES, CONFIRMATION_KINDS, RELATIONSHIP_TYPES, ROUTE_FOR_RELATIONSHIP, SCOPE_CODES,
    AuthorityConfirmation, AuthorityEvidence, AuthorityRegulatoryPack, AuthorityVerification, Organization,
)
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount

RESOURCE = "authority_verification"
# A REJECTED case (decided by a reviewer before review was automated) is
# corrected and resubmitted by the host like ACTION_REQUIRED.
EDITABLE_STATES = ("COLLECTING", "ACTION_REQUIRED", "REJECTED")
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
    "PRINCIPAL_UNCONFIRMED": ("The owner, landlord, company, trust or estate named on the document doesn't match the name you entered. Check the name, or ask them to confirm.", "Add evidence"),
    "PRINCIPAL_NAME_NEEDED": ("Enter the owner's, landlord's, company's, trust's or estate's name exactly as it appears on the document.", "Edit details"),
    "PRINCIPAL_IS_YOU": ("The owner, landlord, company, trust or estate you entered is you. If you own this property, start again and choose Owner; otherwise enter their name.", "Edit details"),
    "PRINCIPAL_NOT_ON_TITLE": ("The property document doesn't show the company, trust or estate you entered as the owner. Check the name, or upload the title that names it.", "Add evidence"),
    "EVIDENCE_UNCLEAR": ("We couldn't read this document clearly. Upload the original PDF or a sharp photo of every page -- flat, well lit, no glare.", "Replace evidence"),
    "DOCUMENT_NOT_OFFICIAL": ("This document says it's a sample or not an official document. Upload the real, issued document.", "Replace evidence"),
    "DOCUMENT_TYPE_UNRECOGNIZED": ("This doesn't look like one of the documents accepted for this step in your country. Upload one of the accepted documents listed.", "Replace evidence"),
    "SCOPE_INSUFFICIENT": ("We could not confirm that this document grants authority to advertise this property.", "Add evidence"),
    "SUBLET_PERMISSION_MISSING": ("Your tenancy alone may not permit subletting. Add the landlord's permission.", "Add consent"),
    "AUTHORITY_DATES_INVALID": ("The authority dates aren't current. Add a current mandate or permission.", "Add evidence"),
    "EVIDENCE_EXPIRED": ("A document has expired. Upload a current one.", "Replace evidence"),
    # Waiting on something outside the host's evidence: checked again automatically.
    "EVIDENCE_UNREADABLE": ("We couldn't read your documents yet. We'll check them again automatically -- you can leave this page.", "View status"),
    "SCAN_INCOMPLETE": ("Your files are still being security-scanned. We'll finish the check automatically -- you can leave this page.", "View status"),
    "PROPERTY_NOT_VERIFIED": ("Your authority is ready; final approval happens automatically once the property is verified.", "Verify property"),
    # Integrity: the host replaces the document with their own original.
    "EVIDENCE_REUSED": ("This document is already used by another account or property. Upload your own original document.", "Replace evidence"),
    "TAMPER_SIGNAL": ("This file looks edited after it was issued. Upload the original document as you received it -- the issuer's PDF or a fresh photo.", "Replace evidence"),
    "CONFLICTING_AUTHORITY": ("Another account already holds verified authority for this property. If you're authorized, ask the owner to confirm you.", "Review status"),
    "ORGANIZATION_UNCONFIRMED": ("Upload a document from the organization you act for that names you and the organization -- an appointment letter or director appointment.", "Add evidence"),
    # Legacy codes on records decided before review was automated.
    "ORGANIZATION_UNVERIFIED": ("We need to verify the organization you act for. You can leave this page.", "View status"),
    "ENTITY_CHAIN": ("Company, trust or estate ownership needs an extra check. You can leave this page.", "View status"),
    "CO_OWNER_CONSENT_MISSING": ("Your co-owner's consent is needed in this country.", "Request consent"),
    "DOCUMENT_ONLY_MANDATE": ("We need to check the mandate. You can leave this page.", "View status"),
    "RECONSIDERATION": ("Your request for reconsideration is being checked.", "View status"),
    "AUTHORITY_EXPIRED": ("Your authority for this property has expired. Renew it to keep listing.", "Renew verification"),
    "AUTHORITY_REVOKED": ("Authority for this property has been withdrawn.", "Review status"),
    "CONFIRMATION_DECLINED": ("The owner or landlord declined the request.", "Review status"),
    # reviewer codes on records decided before review was automated (Section 11.2)
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
    "IDENTITY_REVERIFICATION": ("Your identity needs to be verified again, so your authority for this property must be renewed.", "Renew verification"),
}
REVOCATION_REASONS = ("REVOKED_BY_PRINCIPAL", "REVOKED_BY_HOST", "REVOKED_BY_TRUST_SAFETY", "OWNERSHIP_CHANGED",
                      "PROPERTY_ADDRESS_CHANGED", "IDENTITY_REVERIFICATION")


def describe(codes: list[str]) -> dict:
    for code in codes or []:
        if code in REASONS:
            return {"message": REASONS[code][0], "cta": REASONS[code][1]}
    return {"message": "", "cta": ""}


# -- Authority Regulatory Packs (Section 4) ----------------------------------------

def _req(requirement_id, evidence_class, title, purpose, documents, *, required=True, owner_confirmation=False):
    docs = [{"label": label, "keywords": list(keywords)} for label, keywords in documents]
    return {"requirement_id": requirement_id, "evidence_class": evidence_class, "title": title, "purpose": purpose,
            "documents": docs, "accepted_examples": [d["label"] for d in docs],
            "keywords": _union(d["keywords"] for d in docs),
            "required": required, "owner_confirmation": owner_confirmation}


def _union(keyword_lists) -> list[str]:
    return list(dict.fromkeys(k for keywords in keyword_lists for k in keywords))


_SUBLET_WORDS = ("sublet", "sub let", "sub-let", "sublease", "sub lease", "sub-lease", "subtenant", "sub tenant",
                 "sub-tenant", "underlet")

# Section 4: the documents each country accepts for each requirement, and
# the phrases that identify each one in the document's own text (PDF text
# layer or OCR). The host sees the list; an upload is recognised as the
# first document whose phrase appears (all words of it) -- so a utility bill
# can't pass as a deed, or a plain tenancy as permission to sublet. Packs may
# override this per requirement ("documents"); "*" is the fallback.
DOCUMENT_CATALOG: dict[str, dict[str, tuple[tuple[str, tuple[str, ...]], ...]]] = {
    "IN": {
        "OWNER_PROPERTY_RIGHT": (
            ("Sale deed", ("sale deed", "conveyance deed", "deed of sale", "title deed")),
            ("Gift / partition / settlement deed", ("gift deed", "partition deed", "settlement deed", "release deed")),
            ("Encumbrance certificate", ("encumbrance certificate", "encumbrance")),
            ("Property tax receipt", ("property tax", "house tax", "municipal tax")),
            ("Khata / Patta / mutation record", ("khata", "patta", "mutation", "pahani", "record of rights",
                                                 "adangal", "jamabandi", "7 12 extract")),
            ("Allotment / possession letter", ("allotment letter", "possession letter")),
        ),
        "OWNER_ENTITY_AUTHORITY": (
            ("Board resolution", ("board resolution",)),
            ("Trust deed", ("trust deed",)),
            ("Letters of administration / probate / succession certificate",
             ("letters of administration", "probate", "succession certificate")),
            ("Authorised signatory letter", ("authorised signatory", "authorized signatory")),
        ),
        "CO_OWNER_CONSENT": (
            ("Co-owner's consent letter / NOC", ("consent", "no objection", "noc")),
        ),
        "AGENT_MANDATE": (
            ("Power of attorney", ("power of attorney",)),
            ("Owner's authority letter", ("authority letter", "letter of authority", "authorisation letter",
                                          "authorization letter")),
            ("Property-management agreement", ("management agreement",)),
            ("Agency agreement / mandate", ("agency agreement", "mandate")),
        ),
        "AGENT_ORGANIZATION_LINK": (
            ("Appointment / offer letter", ("appointment letter", "offer letter")),
            ("Employment letter / employee ID", ("employment", "employee")),
            ("Authorised signatory letter", ("authorised signatory", "authorized signatory")),
        ),
        "TENANT_OCCUPATION_RIGHT": (
            ("Rent / lease agreement", ("rental agreement", "rent agreement", "lease agreement", "lease deed",
                                        "tenancy agreement")),
            ("Leave and licence agreement", ("leave and license", "leave and licence", "licensee")),
        ),
        # A general NOC isn't permission to sublet: the document must say so.
        "TENANT_SUBLET_PERMISSION": (
            ("Landlord's NOC / consent to sublet", _SUBLET_WORDS),
        ),
    },
    "GB": {
        "OWNER_PROPERTY_RIGHT": (
            ("HM Land Registry title register / official copy", ("title register", "land registry", "title number",
                                                                 "proprietorship register", "registered proprietor")),
            ("Transfer deed (TR1)", ("transfer deed", "tr1", "transfer of whole")),
            ("Conveyance (unregistered land)", ("conveyance",)),
        ),
        "OWNER_ENTITY_AUTHORITY": (
            ("Board resolution", ("board resolution",)),
            ("Trust deed", ("trust deed",)),
            ("Grant of probate / letters of administration", ("grant of probate", "letters of administration")),
            ("Director appointment", ("director",)),
        ),
        "CO_OWNER_CONSENT": (
            ("Co-owner's written consent", ("consent", "permission", "agree")),
        ),
        "AGENT_MANDATE": (
            ("Letting / management agreement", ("letting agreement", "management agreement", "agency agreement",
                                                "instruction to let")),
            ("Agent's terms of business signed by the landlord", ("terms of business",)),
            ("Power of attorney", ("power of attorney",)),
        ),
        "AGENT_ORGANIZATION_LINK": (
            ("Appointment letter / contract of employment", ("appointment letter", "contract of employment",
                                                             "employment")),
            ("Director appointment", ("director",)),
        ),
        "TENANT_OCCUPATION_RIGHT": (
            ("Assured shorthold tenancy agreement", ("assured shorthold", "tenancy agreement")),
            ("Lease", ("lease",)),
            ("Licence to occupy", ("licence to occupy", "license to occupy")),
        ),
        "TENANT_SUBLET_PERMISSION": (
            ("Landlord's written consent to sublet / underlet",
             _SUBLET_WORDS + ("consent to sublet", "permission to sublet")),
        ),
    },
    "US": {
        "OWNER_PROPERTY_RIGHT": (
            ("Recorded deed (grant / warranty / quitclaim)", ("grant deed", "warranty deed", "quitclaim deed", "deed")),
            ("County assessor / property tax record", ("assessor", "parcel number", "property tax", "tax bill")),
            ("Title insurance policy", ("title insurance",)),
        ),
        "OWNER_ENTITY_AUTHORITY": (
            ("LLC operating agreement / resolution", ("operating agreement", "resolution",
                                                      "certificate of incumbency")),
            ("Trust certificate", ("trust",)),
            ("Letters testamentary", ("letters testamentary",)),
        ),
        "CO_OWNER_CONSENT": (
            ("Co-owner's written consent", ("consent", "agree", "authorize")),
        ),
        "AGENT_MANDATE": (
            ("Property management agreement", ("property management agreement", "management agreement")),
            ("Exclusive leasing / listing agreement", ("listing agreement", "leasing agreement", "exclusive right")),
            ("Power of attorney", ("power of attorney",)),
        ),
        "AGENT_ORGANIZATION_LINK": (
            ("Offer / employment letter", ("offer letter", "employment")),
            ("Broker / contractor agreement", ("broker", "independent contractor")),
        ),
        "TENANT_OCCUPATION_RIGHT": (
            ("Residential lease agreement", ("lease agreement", "residential lease", "rental agreement", "lease")),
        ),
        "TENANT_SUBLET_PERMISSION": (
            ("Landlord's written consent to sublease", ("sublet", "sublease", "sub-lease", "sub lease", "subtenant",
                                                        "sub-tenant")),
        ),
    },
    "*": {
        "OWNER_PROPERTY_RIGHT": (
            ("Title / land registry extract", ("land registry", "title", "proprietor")),
            ("Deed", ("deed",)),
            ("Property tax record", ("property tax",)),
            ("Court, estate or trust record", ("ownership", "estate", "court")),
        ),
        "OWNER_ENTITY_AUTHORITY": (
            ("Board resolution", ("resolution",)),
            ("Trust deed", ("trust",)),
            ("Letters of administration", ("administration",)),
            ("Authorised signatory letter", ("authorized signatory", "authorised signatory")),
        ),
        "CO_OWNER_CONSENT": (
            ("Co-owner's written consent", ("consent", "no objection", "agree")),
        ),
        "AGENT_MANDATE": (
            ("Power of attorney", ("power of attorney",)),
            ("Agency / letting / management agreement", ("management agreement", "agency agreement",
                                                         "letting agreement", "leasing agreement")),
            ("Signed owner mandate / authority letter", ("mandate", "letter of authority", "authority letter",
                                                         "authorization letter", "authorisation letter")),
        ),
        "AGENT_ORGANIZATION_LINK": (
            ("Appointment / employment letter", ("appointment", "employment", "employee")),
            ("Director appointment", ("director",)),
        ),
        "TENANT_OCCUPATION_RIGHT": (
            ("Tenancy / lease agreement", ("lease", "tenancy", "rental agreement", "rent agreement")),
            ("Licence to occupy", ("licence", "license")),
        ),
        "TENANT_SUBLET_PERMISSION": (
            ("Landlord's written consent to sublet", _SUBLET_WORDS),
        ),
    },
}


def default_documents(country: str, requirement_id: str) -> list[dict]:
    table = DOCUMENT_CATALOG.get((country or "").upper()) or DOCUMENT_CATALOG["*"]
    entries = table.get(requirement_id) or DOCUMENT_CATALOG["*"].get(requirement_id, ())
    return [{"label": label, "keywords": list(keywords)} for label, keywords in entries]


def default_keywords(country: str, requirement_id: str) -> list[str]:
    return _union(d["keywords"] for d in default_documents(country, requirement_id))


def requirement_documents(req: dict, country: str) -> list[dict]:
    """The pack's own document list, else the country defaults."""
    return req.get("documents") or default_documents(country, req["requirement_id"])


def detect_document(text: str, documents: list[dict], tolerant: bool = False) -> str | None:
    """The first listed document whose identifying phrase is in the text."""
    for doc in documents:
        if doc.get("keywords") and _keywords_in_text(text, doc["keywords"], tolerant):
            return doc["label"]
    return None


def _routes(country: str) -> dict:
    def docs(rid):
        return [(d["label"], d["keywords"]) for d in default_documents(country, rid)]

    return {
        "OWNER": [
            _req("OWNER_PROPERTY_RIGHT", "PROPERTY_RIGHT", "Property right / ownership",
                 "Shows you own (or hold a right in) this property. An address document alone does not prove ownership.",
                 docs("OWNER_PROPERTY_RIGHT")),
            _req("OWNER_ENTITY_AUTHORITY", "ORGANIZATION_LINK", "Authority to act for the owning company / trust",
                 "Needed only when the property is owned by a company, trust or estate.",
                 docs("OWNER_ENTITY_AUTHORITY"), required=False),
            _req("CO_OWNER_CONSENT", "SUBLET_PERMISSION", "Co-owner consent",
                 "Where required, your co-owner(s) agree to the listing.", docs("CO_OWNER_CONSENT"),
                 required=False, owner_confirmation=True),
        ],
        "AGENT": [
            _req("AGENT_MANDATE", "MANDATE", "Owner / principal mandate for this property",
                 "Shows the owner or authorized principal permits you to advertise and rent this property.",
                 docs("AGENT_MANDATE"), owner_confirmation=True),
            _req("AGENT_ORGANIZATION_LINK", "ORGANIZATION_LINK", "Your role at the organization",
                 "Needed only when you act through a company. A company registration is not a property mandate.",
                 docs("AGENT_ORGANIZATION_LINK"), required=False),
        ],
        "SUBLET": [
            _req("TENANT_OCCUPATION_RIGHT", "OCCUPATION_RIGHT", "Current right to occupy",
                 "Shows you currently hold a tenancy, lease or license for this property.",
                 docs("TENANT_OCCUPATION_RIGHT")),
            _req("TENANT_SUBLET_PERMISSION", "SUBLET_PERMISSION", "Permission to sublet",
                 "Your tenancy alone may not permit subletting. Shows the landlord or authorized agent allows it.",
                 docs("TENANT_SUBLET_PERMISSION"), owner_confirmation=True),
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


def pack_requirements_view(pack: AuthorityRegulatoryPack) -> dict:
    """The pack's requirements with each one's document list resolved."""
    return {route: [{**r, "documents": requirement_documents(r, pack.country_code)} for r in reqs]
            for route, reqs in (pack.requirements or {}).items()}


def _validated_documents(rid: str, documents) -> list[dict]:
    if not isinstance(documents, list) or not 1 <= len(documents) <= 20:
        raise ValueError(f"{rid}: list 1 to 20 accepted documents")
    out = []
    for doc in documents:
        label = str((doc or {}).get("label", "")).strip() if isinstance(doc, dict) else ""
        keywords = doc.get("keywords") if isinstance(doc, dict) else None
        if not label or len(label) > 120:
            raise ValueError(f"{rid}: every document needs a name (up to 120 characters)")
        if (not isinstance(keywords, list) or not keywords or len(keywords) > 30
                or not all(isinstance(k, str) and k.strip() and len(k) <= 120 for k in keywords)):
            raise ValueError(f"{rid} / {label}: 1 to 30 identifying phrases are needed to recognise the document")
        out.append({"label": label, "keywords": list(dict.fromkeys(k.strip().lower() for k in keywords))})
    return out


def _validated_requirements(new: dict, current: dict, country: str) -> dict:
    """Trust & Safety may change a requirement's wording, its accepted
    documents (name + identifying phrases each) and whether it's required /
    offers owner confirmation -- not add, drop or rename requirements, which
    the decision logic relies on."""
    if not isinstance(new, dict) or set(new) != set(current or {}):
        raise ValueError("requirements must keep the routes OWNER, AGENT and SUBLET")
    out = {}
    for route, reqs in new.items():
        known = [r["requirement_id"] for r in current[route]]
        if not isinstance(reqs, list) or [r.get("requirement_id") for r in reqs if isinstance(r, dict)] != known:
            raise ValueError(f"{route} must keep its requirements, in order: {', '.join(known)}")
        cleaned = []
        for req, old in zip(reqs, current[route]):
            documents = _validated_documents(old["requirement_id"],
                                             req.get("documents", requirement_documents(old, country)))
            cleaned.append({
                **old,
                "title": str(req.get("title", old["title"])).strip()[:120] or old["title"],
                "purpose": str(req.get("purpose", old["purpose"])).strip()[:400] or old["purpose"],
                "documents": documents,
                "accepted_examples": [d["label"] for d in documents],
                "keywords": _union(d["keywords"] for d in documents),
                "required": bool(req.get("required", old["required"])),
                "owner_confirmation": bool(req.get("owner_confirmation", old["owner_confirmation"])),
            })
        out[route] = cleaned
    return out


def update_pack(db: Session, pack: AuthorityRegulatoryPack, changes: dict) -> AuthorityRegulatoryPack:
    editable = ("country_name", "requirements", "terminology", "sublet_consent_required", "co_owner_consent_required",
                "parallel_identity_intake", "default_validity_days", "expiring_soon_days", "evidence_retention_days",
                "listing_control")
    unknown = set(changes) - set(editable)
    if unknown:
        raise ValueError(f"Not editable: {sorted(unknown)}")
    if "listing_control" in changes and changes["listing_control"] not in ("SUSPEND", "NONE"):
        raise ValueError("listing_control must be SUSPEND or NONE")
    if "requirements" in changes:
        changes = {**changes, "requirements": _validated_requirements(changes["requirements"], pack.requirements, pack.country_code)}
    for field, low, high in (("default_validity_days", 30, 1825), ("expiring_soon_days", 1, 180)):
        if field in changes and not (isinstance(changes[field], int) and low <= changes[field] <= high):
            raise ValueError(f"{field} must be between {low} and {high}")
    retention = changes.get("evidence_retention_days")
    if retention is not None and not (isinstance(retention, int) and retention >= 30):
        raise ValueError("evidence_retention_days must be at least 30 (or empty to keep evidence)")
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
    if v.state in ("ACTION_REQUIRED", "REJECTED"):
        v.state = "COLLECTING"


def _set_state(db: Session, v: AuthorityVerification, new_state: str) -> str:
    allowed = {
        "COLLECTING": {"SUBMITTED", "SUPERSEDED"},
        "SUBMITTED": {"VERIFIED", "ACTION_REQUIRED"},
        # Legacy cases from the retired review queue are decided automatically.
        "MANUAL_REVIEW": {"SUBMITTED", "VERIFIED", "ACTION_REQUIRED", "SUPERSEDED"},
        "ACTION_REQUIRED": {"COLLECTING", "SUBMITTED", "SUPERSEDED"},
        "VERIFIED": {"EXPIRED", "REVOKED", "SUPERSEDED"},
    }
    previous = v.state
    if new_state not in allowed.get(previous, set()):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Authority can't move from {previous} to {new_state}")
    v.state = new_state
    return previous


def _expiring_soon_days(db: Session | None, country_code: str) -> int:
    """The pack's renewal threshold, read without seeding (this runs on every
    status read)."""
    if db is None:
        return 30
    for code in ((country_code or "").upper(), "*"):
        days = db.scalar(select(AuthorityRegulatoryPack.expiring_soon_days).where(
            AuthorityRegulatoryPack.country_code == code, AuthorityRegulatoryPack.active.is_(True)))
        if days is not None:
            return days
    return 30


def effective_state(v: AuthorityVerification | None, now: datetime | None = None) -> str:
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


def country_for(prop: Property) -> str:
    from app.services.location import country_for_jurisdiction

    return prop.country_code or country_for_jurisdiction(prop.jurisdiction_code)


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
        sources = [{"code": a.code, "source_type": a.source_type, "label": a.label}
                   for a in sources_for(v.country_code, req["evidence_class"])]
        documents = requirement_documents(req, pack.country_code)
        out.append({**req, "documents": documents, "accepted_examples": [d["label"] for d in documents],
                    "keywords": _union(d["keywords"] for d in documents), "required": required, "status": req_status, "sources": sources,
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


# A text layer shorter than this is a scan with a stray label, not a document.
MIN_TEXT_LAYER_CHARS = 20
# Below this mean Tesseract word confidence an OCR read is too unreliable to
# match on; the host is asked for a clearer copy (same bar as property evidence).
OCR_MIN_CONFIDENCE = 55.0


def document_text(content: bytes, content_type: str) -> tuple[str, str, float | None]:
    """-> (text, source, ocr confidence). Source: PDF_TEXT (a digital PDF's
    text layer), OCR (Tesseract on a photo / scan, first pages of a PDF),
    OCR_UNAVAILABLE (no OCR engine on this server) or NONE (OCR failed)."""
    text = _pdf_text(content, content_type)
    if len(text.strip()) >= MIN_TEXT_LAYER_CHARS:
        return text, "PDF_TEXT", None
    try:
        from app.services import document_ocr

        if not document_ocr.is_available():
            return "", "OCR_UNAVAILABLE", None
        ocr_text, confidence = document_ocr.ocr_document(content)
        return ocr_text, "OCR", round(confidence, 1)
    except Exception:
        import logging

        logging.getLogger(__name__).info("authority evidence OCR failed", exc_info=True)
        return "", "NONE", None


def _readable(text: str, source: str, confidence: float | None) -> bool:
    if not text.strip():
        return False
    return source == "PDF_TEXT" or (source == "OCR" and (confidence or 0) >= OCR_MIN_CONFIDENCE)


def _has(words: set[str], token: str, tolerant: bool) -> bool:
    from app.services.property_document_analysis import _token_present

    return _token_present(token, words, tolerant)


def _property_in_text(text: str, prop: Property, tolerant: bool = False) -> bool:
    """The document names this property: its postal code (when known) plus
    either most of the address line, or -- the same rule as property
    verification -- the house / door number and a place name, ignoring
    landmark words the host added ("2-599 Muthyalamma temple" matches a
    receipt for "2-599 MADUPALLY, MADHIRA ... 507203")."""
    from app.services.property_document_analysis import identity_matches

    body = _norm(text)
    if not body:
        return False
    postal = (prop.postal_code or "").replace(" ", "").lower()
    if postal and postal not in body.replace(" ", ""):
        return False
    words = set(body.split())
    line1 = _norm(prop.address_line_1 or prop.address)
    tokens = [t for t in line1.split() if len(t) > 1 or t.isdigit()]
    if tokens and sum(1 for t in tokens if _has(words, t, tolerant)) / len(tokens) >= 0.75:
        return True
    address = {"address_line_1": prop.address_line_1 or prop.address, "address_line_2": prop.address_line_2 or "",
               "locality": prop.locality or prop.city or "", "postal_code": prop.postal_code or "",
               "country_code": prop.country_code or ""}
    return identity_matches(text, address, tolerant)


def _name_in_text(text: str, name: str, tolerant: bool = False) -> bool:
    """Every part of the name appears -- as its own word, or joined to
    another part the way documents often write it ("ANIL KUMAR" ->
    "ANILKUMAR"), in any order. A joined word counts only when it's made
    entirely of the name's own parts, so "RAO" isn't found in "RAOBERT"."""
    parts = [p for p in _norm(name).split() if len(p) > 1]
    words = set(_norm(text).split())

    def composed(word: str) -> bool:
        """word is two or more of the name's parts written together."""
        def rest(w: str, used: int) -> bool:
            if not w:
                return used >= 2
            return any(w.startswith(p) and rest(w[len(p):], used + 1) for p in parts)
        return rest(word, 0)

    joined = [w for w in words if len(w) > 3 and composed(w)]

    def found(part: str) -> bool:
        return _has(words, part, tolerant) or any(part in w for w in joined)

    return bool(parts) and all(found(p) for p in parts)


# A document that says it isn't real can't establish anything, whatever it
# names. Phrases, not single words: "void" or "sample" alone appear in
# genuine deeds ("null and void", "soil sample").
NOT_OFFICIAL_MARKERS = (
    # ("specimen signature" is normal on a power of attorney, so only these.)
    "sample receipt", "sample document", "sample copy", "sample certificate", "sample agreement",
    "specimen copy", "specimen document", "specimen only",
    "not an official document", "not an official", "not valid for any", "not valid for legal",
    "testing purposes", "test document", "demonstration purposes", "demo purposes", "dummy document",
    "for illustration only", "illustrative purposes",
)


def _not_official(text: str) -> bool:
    body = " ".join(_norm(text).split())
    return any(" ".join(_norm(marker).split()) in body for marker in NOT_OFFICIAL_MARKERS)


def _keywords_in_text(text: str, keywords: list[str], tolerant: bool = False) -> bool:
    """Any one accepted-document phrase appears (every word of it)."""
    words = set(_norm(text).split())
    for phrase in keywords:
        tokens = [t for t in _norm(phrase).split() if t]
        if tokens and all(_has(words, t, tolerant and len(t) >= 5) for t in tokens):
            return True
    return False


HEIC_BRANDS = (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1")


def is_heic(content: bytes) -> bool:
    return content[4:8] == b"ftyp" and content[8:12] in HEIC_BRANDS


def heic_to_jpeg(content: bytes) -> bytes:
    """Section 8.2 accepts HEIC (iPhone photos). It's converted to JPEG once,
    on upload, so OCR, metadata stripping and the evidence viewer all work
    on an ordinary image. Anything that isn't HEIC passes through untouched."""
    if not is_heic(content):
        return content
    try:
        import io

        from PIL import Image
        from pillow_heif import register_heif_opener

        register_heif_opener()
        image = Image.open(io.BytesIO(content))
        image.load()
        out = io.BytesIO()
        image.convert("RGB").save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception:  # missing library or a damaged file
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "We couldn't read this HEIC photo -- upload it as a JPG or PDF instead")


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
    content = heic_to_jpeg(content)
    sniffed = _sniff(content)
    if not sniffed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported file -- upload a PDF, JPG, PNG or HEIC")
    extension, content_type = sniffed
    from app.core import upload_scan

    scan_status = upload_scan.inspect(content, content_type)  # Section 13: rejects unsafe files
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
    evidence = AuthorityEvidence(
        verification_id=v.id, requirement_id=requirement_id, evidence_type=reqs[requirement_id]["evidence_class"],
        source_type="UPLOAD", issuer=issuer.strip()[:200], document_reference=document_reference.strip()[:200],
        issued_at=_parse_dt(issued_at), expires_at=_parse_dt(expires_at), stored_filename=stored,
        original_filename=Path(original_filename or "document").name[:255], content_type=content_type,
        file_size=len(content), file_hash=file_hash, reused_elsewhere=reused > 0, tamper_signal=tamper,
        processing_status="READY", scan_status=scan_status,
    )
    _analyze(db, v, evidence, content, reqs[requirement_id]["documents"])
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
    if evidence is None or evidence.verification_id != v.id or evidence.source_type == "OWNER_CONFIRMATION":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if evidence.stored_filename:
        (Path(settings.authority_upload_dir) / evidence.stored_filename).unlink(missing_ok=True)
    _event(db, "AUTHORITY_EVIDENCE_REMOVED", v, actor=user,
           extra={"evidenceId": evidence.id, "requirementId": evidence.requirement_id, "fileHash": evidence.file_hash})
    db.delete(evidence)
    _touch(v)
    db.commit()


# -- trusted sources: registry / connected property-management system (Section 4.1) --

class SourceAdapter:
    """A trusted digital source (land registry API, property-management
    system). Register an implementation in SOURCE_ADAPTERS; requirements
    then offer it to hosts in that country (Sections 5.2 O1, 6.3 A2, 7.3 S1).

    lookup() returns {"found": bool, "issuer": str, "reference": str,
    "property_matched": bool | None, "holder_name": str,
    "principal_name": str, "expires_at": datetime | None}."""

    code = ""
    source_type = "REGISTRY"  # REGISTRY | CONNECTOR
    countries: tuple[str, ...] = ()
    evidence_classes: tuple[str, ...] = ()
    label = ""

    def lookup(self, prop: Property, reference: str, evidence_class: str) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


SOURCE_ADAPTERS: dict[str, SourceAdapter] = {}


def sources_for(country: str, evidence_class: str) -> list[SourceAdapter]:
    return [a for a in SOURCE_ADAPTERS.values()
            if evidence_class in a.evidence_classes and (country or "").upper() in a.countries]


def check_source(db: Session, user: UserAccount, v: AuthorityVerification, *, requirement_id: str, source_code: str,
                 reference: str, expected_version: int | None = None, correlation_id: str = "") -> AuthorityEvidence:
    """Query a trusted source and record its answer as SOURCE_ASSERTION-backed
    evidence for the requirement. The result is matched like any other
    evidence -- a source hit for a different person or property doesn't
    verify anything."""
    _check_version(v, expected_version)
    _editable(v)
    reqs = {r["requirement_id"]: r for r in requirements(db, v)}
    req = reqs.get(requirement_id)
    adapter = SOURCE_ADAPTERS.get(source_code)
    if req is None or adapter is None or adapter not in sources_for(v.country_code, req["evidence_class"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That source isn't available for this requirement")
    if not reference.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the record reference")
    prop = db.get(Property, v.property_id)
    try:
        result = adapter.lookup(prop, reference.strip()[:200], req["evidence_class"])
    except Exception:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "That source isn't responding right now -- try again later or upload a document instead")
    if not result.get("found"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No record was found for that reference")
    my_name = _verified_legal_name(db, v.party_id)
    holder = result.get("holder_name") or ""
    principal = principal_name(v)
    evidence = AuthorityEvidence(
        verification_id=v.id, requirement_id=requirement_id, evidence_type=req["evidence_class"],
        source_type=adapter.source_type, issuer=(result.get("issuer") or adapter.label)[:200],
        document_reference=(result.get("reference") or reference)[:200], expires_at=_utc(result.get("expires_at")),
        readable=True, property_matched=result.get("property_matched"),
        name_matched=_name_in_text(holder, my_name) if holder and my_name else None,
        principal_matched=_name_in_text(result.get("principal_name") or "", principal) if principal else None,
        processing_status="READY", scan_status="CLEAN",
    )
    db.add(evidence)
    db.flush()
    _event(db, "AUTHORITY_EVIDENCE_UPLOADED", v, actor=user, correlation_id=correlation_id,
           extra={"evidenceId": evidence.id, "requirementId": requirement_id, "sourceType": adapter.source_type,
                  "source": adapter.code})
    _touch(v)
    db.commit()
    db.refresh(evidence)
    return evidence


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

HOST_MATCH_KEYS = ("property_match", "representative_match", "principal_match", "scope_match", "validity_result")


def host_match_results(db: Session, v: AuthorityVerification) -> dict:
    """The O2 / A3 / S3 review lines the host sees (match / no match /
    not checked yet). Only the five Section 4.3 outcomes -- never integrity,
    reuse or tamper signals (Section 11.1: no internal risk signals)."""
    model = _match_model(db, v)
    return {key: model.get(key) for key in HOST_MATCH_KEYS}


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
    principal_on_title = None
    if v.relationship_type == "REPRESENTATIVE":
        # Entity chain: the title names the company / trust / estate (the
        # principal); the entity-authority document names that principal
        # and the verified person acting for it.
        entity = [e for e in live if e.requirement_id == "OWNER_ENTITY_AUTHORITY"]
        representative = tri([e.name_matched for e in entity])
        principal = tri([e.principal_matched for e in entity])
        principal_on_title = tri([e.principal_matched for e in primary])
    elif route == "OWNER":
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
            "principal_on_title": principal_on_title, "scope_match": scope,
            "validity_result": validity and not evidence_expired,
            "source_integrity": integrity, "owner_confirmation": bool(confirmed),
            # OCR ran (or the PDF had text) but the read was too poor to match on.
            "unclear_documents": any(not e.readable and e.text_source != "OCR_UNAVAILABLE"
                                     for e in live if e.source_type == "UPLOAD"),
            # This server has no OCR engine -- infrastructure, not the host's fault.
            "ocr_unavailable": any(e.text_source == "OCR_UNAVAILABLE" for e in live if e.source_type == "UPLOAD")}


def _wrong_document_types(v: AuthorityVerification, reqs: list[dict]) -> list[str]:
    """Requirements whose readable uploads are none of the documents the
    country accepts for them (no pack keyword in the text), and that nothing
    else (a confirmation or trusted source) satisfies."""
    wrong = []
    for req in reqs:
        items = [e for e in v.evidence if e.requirement_id == req["requirement_id"] and e.processing_status == "READY"]
        uploads = [e for e in items if e.source_type == "UPLOAD" and e.readable]
        if not uploads or any(e.source_type != "UPLOAD" for e in items) or req.get("confirmed"):
            continue
        if not any(e.type_matched is not False for e in uploads):
            wrong.append(req["requirement_id"])
    return wrong


def _analyze(db: Session, v: AuthorityVerification, e: AuthorityEvidence, content: bytes, documents: list[dict]) -> None:
    """Read the document (text layer, else OCR) and record what it matches --
    only the outcomes are kept, never the extracted text (Section 13)."""
    text, source, confidence = document_text(content, e.content_type)
    readable = _readable(text, source, confidence)
    tolerant = source == "OCR"
    my_name = _verified_legal_name(db, v.party_id)
    principal = principal_name(v)
    e.text_source, e.ocr_confidence, e.readable = source, confidence, readable
    # Which listed document this is (Section 4), identified from its own text.
    e.detected_document = (detect_document(text, documents, tolerant) or "")[:120] if readable else ""
    e.type_matched = bool(e.detected_document) if readable and documents else None
    e.not_official = readable and _not_official(text)
    e.property_matched = _property_in_text(text, db.get(Property, v.property_id), tolerant) if readable else None
    e.name_matched = _name_in_text(text, my_name, tolerant) if readable and my_name else None
    e.principal_matched = _name_in_text(text, principal, tolerant) if readable and principal else None


def _reanalyze_stale_uploads(db: Session, v: AuthorityVerification, reqs: list[dict]) -> None:
    """Uploads read before OCR / document-type checks existed (text_source
    blank), while this server had no OCR engine, or whose name didn't match
    are read again -- so an old result doesn't stick after the matching
    improves (e.g. joined names like "ANILKUMAR")."""
    documents = {r["requirement_id"]: r["documents"] for r in reqs}
    for e in v.evidence:
        stale = e.text_source in ("", "OCR_UNAVAILABLE", "NONE") or (
            e.readable and (e.name_matched is False or e.property_matched is False))
        if e.source_type != "UPLOAD" or e.processing_status != "READY" or not stale:
            continue
        content = read_evidence(e)
        if content is not None:
            _analyze(db, v, e, content, documents.get(e.requirement_id, []))


def _refresh_principal_matches(db: Session, v: AuthorityVerification) -> None:
    """The principal's name is often entered after the upload: match the
    stored documents against it now (re-read, never kept as text)."""
    principal = principal_name(v)
    if not principal:
        return
    types = ("MANDATE", "SUBLET_PERMISSION")
    if v.relationship_type == "REPRESENTATIVE":
        types += ("PROPERTY_RIGHT", "ORGANIZATION_LINK")
    for e in v.evidence:
        # Not yet matched, or matched against a name the host has since corrected.
        if (e.source_type != "UPLOAD" or e.processing_status != "READY" or not e.readable
                or e.principal_matched is True or e.evidence_type not in types):
            continue
        content = read_evidence(e)
        if content is None:
            continue
        text, source, _confidence = document_text(content, e.content_type)
        e.principal_matched = _name_in_text(text, principal, source == "OCR")


def _conflicts(db: Session, v: AuthorityVerification) -> bool:
    """Another party already holds current verified authority over the same
    property (Section 9.2). Claims still being checked don't block: the
    first one verified controls, and a later one has to be confirmed by the
    owner."""
    now = _now()
    return (db.scalar(select(func.count(AuthorityVerification.id)).where(
        AuthorityVerification.property_id == v.property_id, AuthorityVerification.party_id != v.party_id,
        AuthorityVerification.state == "VERIFIED",
        (AuthorityVerification.expires_at.is_(None)) | (AuthorityVerification.expires_at > now))) or 0) > 0


def _principal_is_host(db: Session, v: AuthorityVerification) -> bool:
    """The principal entered is the host's own verified name (same words,
    any order)."""
    principal, mine = _norm(principal_name(v)).split(), _norm(_verified_legal_name(db, v.party_id)).split()
    return bool(principal) and sorted(principal) == sorted(mine)


# Legal-form words left out when looking for an organization's name in a
# document ("Lake Lettings Ltd" is "LAKE LETTINGS LIMITED" on a letterhead).
_LEGAL_FORM_WORDS = {"ltd", "limited", "llp", "plc", "inc", "llc", "pvt", "private", "co", "company", "corp",
                     "corporation", "the"}


def _organization_core_name(name: str) -> str:
    words = [w for w in _norm(name).split() if w not in _LEGAL_FORM_WORDS]
    return " ".join(words) or _norm(name)


def _organization_confirmed(db: Session, v: AuthorityVerification) -> bool:
    """The organization the host acts through, from the documents alone: an
    already verified organization is reused; otherwise a readable link
    document (appointment letter, director appointment, board resolution)
    must name both the verified person and the organization. Confirming it
    here verifies the organization for reuse on the host's other properties."""
    org = v.organization
    if org is None:
        return False
    if org.status == "VERIFIED":
        return True
    core = _organization_core_name(org.name)
    for e in v.evidence:
        if (e.source_type != "UPLOAD" or e.processing_status != "READY" or not e.readable
                or e.name_matched is not True or e.type_matched is False
                or e.requirement_id not in ("AGENT_ORGANIZATION_LINK", "OWNER_ENTITY_AUTHORITY")):
            continue
        content = read_evidence(e)
        if content is None:
            continue
        text, source, _confidence = document_text(content, e.content_type)
        if _name_in_text(text, core, source == "OCR"):
            org.status, org.verified_at = "VERIFIED", _now()
            return True
    return False


def decide(db: Session, v: AuthorityVerification) -> tuple[str, str, list[str]]:
    """-> (state, assurance level, reason codes). Fully automatic: approves
    a complete, matching, current, uncontested chain read from the
    documents; otherwise shows the host the specific fix (ACTION_REQUIRED --
    never a final rejection); waits (stays SUBMITTED) only for things the
    host can't fix -- property verification, the OCR engine, the malware
    scanner. Nothing goes to a manual review queue."""
    pack = get_pack(db, v.country_code)
    _reanalyze_stale_uploads(db, v, requirements(db, v))
    _refresh_principal_matches(db, v)
    m = _match_model(db, v)
    v.match_results = m
    route = route_for(v)
    action, waiting = [], []
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
    # Sections 4 / 11.1: the document is read (text layer or OCR) and must be
    # an accepted document for this country that names the property and the
    # verified person. A poor read or the wrong document is the host's to fix.
    if m["unclear_documents"]:
        action.append("EVIDENCE_UNCLEAR")
    if _wrong_document_types(v, reqs):
        action.append("DOCUMENT_TYPE_UNRECOGNIZED")
    if any(e.not_official for e in v.evidence if e.processing_status == "READY" and e.source_type == "UPLOAD"):
        action.append("DOCUMENT_NOT_OFFICIAL")
    if m["ocr_unavailable"]:
        waiting.append("EVIDENCE_UNREADABLE")  # no OCR engine on this server
    elif not m["unclear_documents"] and (m["property_match"] is None or m["representative_match"] is None):
        waiting.append("EVIDENCE_UNREADABLE")  # e.g. no verified legal name to match against yet
    if any(e.scan_status == "ERROR" for e in v.evidence if e.processing_status == "READY"):
        waiting.append("SCAN_INCOMPLETE")
    if _conflicts(db, v) and not m["owner_confirmation"]:
        action.append("CONFLICTING_AUTHORITY")
    if v.acting_capacity == "ORGANIZATION" and not _organization_confirmed(db, v):
        action.append("ORGANIZATION_UNCONFIRMED")
    readable = not m["unclear_documents"] and not m["ocr_unavailable"]
    # Acting for someone else (agent, tenant, representative): that someone
    # can't be the host -- an owner uses the Owner route.
    if (route != "OWNER" or v.relationship_type == "REPRESENTATIVE") and _principal_is_host(db, v):
        action.append("PRINCIPAL_IS_YOU")
    if v.relationship_type == "REPRESENTATIVE" and readable:
        # Entity chain (company / trust / estate), checked from the documents.
        if not principal_name(v):
            action.append("PRINCIPAL_NAME_NEEDED")
        else:
            if m["principal_on_title"] is False:
                action.append("PRINCIPAL_NOT_ON_TITLE")
            if m["principal_match"] is False:
                action.append("PRINCIPAL_UNCONFIRMED")
    # Agent mandate / sublet permission: without the owner's or landlord's own
    # confirmation, the document itself must name the principal the host
    # identified (a tenant can't self-evidence permission -- Section 13.1).
    needs_principal = route == "AGENT" or (route == "SUBLET" and pack.sublet_consent_required)
    if needs_principal and not m["owner_confirmation"] and not m["unclear_documents"]:
        if not principal_name(v):
            action.append("PRINCIPAL_NAME_NEEDED")
        elif m["principal_match"] is not True:
            action.append("PRINCIPAL_UNCONFIRMED")
    if not _property_verified(db, v.property_id):
        waiting.append("PROPERTY_NOT_VERIFIED")
    if not m["source_integrity"]:
        # The host replaces the document with their own original.
        action.insert(0, "EVIDENCE_REUSED" if any(e.reused_elsewhere for e in v.evidence
                                                  if e.processing_status == "READY") else "TAMPER_SIGNAL")
    if action:
        return "ACTION_REQUIRED", "AV-X", list(dict.fromkeys(action + waiting))
    if waiting:
        return "SUBMITTED", "AV-0", list(dict.fromkeys(waiting))
    # Section 2.3: two independent sources -- a matching document plus an
    # authenticated principal confirmation -- is enhanced assurance.
    has_document = any(e.source_type == "UPLOAD" and e.processing_status == "READY" for e in v.evidence)
    return "VERIFIED", "AV-2" if has_document and m["owner_confirmation"] else "AV-1", []


def _apply_decision(db: Session, v: AuthorityVerification, *, actor=None, correlation_id: str = "") -> str:
    """Runs the automated decision and moves the case to its outcome. On a
    re-check (no actor) nothing is recorded or sent unless the outcome
    changed. Returns the resulting state."""
    previous_state, previous_codes = v.state, list(v.reason_codes or [])
    state, level, codes = decide(db, v)
    if actor is None and state == previous_state and codes == previous_codes:
        return state
    _event(db, "AUTHORITY_AUTOMATED_CHECK_COMPLETED", v, actor=actor, correlation_id=correlation_id,
           reason_codes=codes, extra={"outcome": state, "matchResults": {k: v.match_results.get(k) for k in (
               "property_match", "representative_match", "principal_match", "scope_match", "validity_result")}})
    v.reason_codes = codes
    now = _now()
    if state == "VERIFIED":
        _approve(db, v, assurance=level, actor=actor, correlation_id=correlation_id)
    elif state == "SUBMITTED":
        if v.state != "SUBMITTED":  # a legacy case from the retired review queue
            _set_state(db, v, "SUBMITTED")
        v.assurance_level = level
        _event(db, "AUTHORITY_CHECK_WAITING", v, actor=actor, reason_codes=codes, previous_state=previous_state,
               new_state="SUBMITTED", correlation_id=correlation_id)
        _notify(db, v, "Checking your authority", describe(codes)["message"],
                email_status="Checking" if actor is not None else "", email_variant="submitted")
    else:
        _set_state(db, v, "ACTION_REQUIRED")
        v.assurance_level = level
        v.decided_at = now
        _event(db, "AUTHORITY_ACTION_REQUIRED", v, actor=actor, reason_codes=codes, previous_state=previous_state,
               new_state="ACTION_REQUIRED", correlation_id=correlation_id)
        _notify(db, v, "Authority action required", describe(codes)["message"], email_status="Action required",
                email_variant="action-required")
    return state


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
    v.attested_at = v.submitted_at = _now()
    v.submit_idempotency_key = idempotency_key
    _apply_decision(db, v, actor=user, correlation_id=correlation_id)
    _touch(v)
    db.commit()
    db.refresh(v)
    return v


def _rescan_incomplete(v: AuthorityVerification) -> None:
    """Uploads the malware scanner couldn't answer for are scanned again; an
    infected file needs replacing."""
    from app.core import upload_scan

    for e in v.evidence:
        if e.source_type != "UPLOAD" or e.processing_status != "READY" or e.scan_status != "ERROR":
            continue
        content = read_evidence(e)
        if content is None:
            continue
        try:
            e.scan_status = upload_scan.inspect(content, e.content_type)
        except HTTPException:
            e.scan_status, e.processing_status = "INFECTED", "NEEDS_REPLACEMENT"


def recheck_pending(db: Session, *, property_id: int | None = None, correlation_id: str = "") -> int:
    """Decides waiting cases again (and any left in the retired review
    queue): run by the scheduler and as soon as a property is verified.
    Returns how many changed state; the caller commits."""
    query = select(AuthorityVerification).where(AuthorityVerification.state.in_(("SUBMITTED", "MANUAL_REVIEW")))
    if property_id is not None:
        query = query.where(AuthorityVerification.property_id == property_id)
    changed = 0
    for v in list(db.scalars(query.order_by(AuthorityVerification.id))):
        previous = v.state
        _rescan_incomplete(v)
        if _apply_decision(db, v, correlation_id=correlation_id) != previous:
            _touch(v)
            changed += 1
    return changed


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
    _notify(db, v, "Listing authority verified", "Your authority to list this property has been verified.",
            email_status="Verified", email_variant="approved")


def _notify(db: Session, v: AuthorityVerification, title: str, message: str, *, email_status: str = "",
            email_variant: str = "") -> None:
    """In-app notice, plus the ZR-EML-VER-002 email for the Section 15.3
    states (submitted, action required, approved, expiring, expired, revoked)."""
    from app.crud import notification as notif_crud

    notif_crud.notify_user_by_party(db, v.party_id, title=title, message=message,
                                    notification_type="authority.status", related_entity_type=RESOURCE,
                                    related_entity_id=str(v.id))
    if not email_status:
        return
    from app.core.mailer import send_authority_status_email
    from app.crud.user import get_user_by_party_id

    user = get_user_by_party_id(db, v.party_id)
    if user is None:
        return
    prop = db.get(Property, v.property_id)
    expires = _utc(v.expires_at)
    send_authority_status_email(
        user.email, user.full_name, status_display=email_status, variant=email_variant, message=message,
        property_label=f"{prop.address or prop.city} · Property #{prop.id}", verification_id=v.id,
        valid_until=f"{expires:%d %b %Y}" if expires and v.state == "VERIFIED" else "")


# -- revoke / renew / reconsider (Sections 9.2, 11) ---------------------------------

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
            (f" {suspended} listing(s) paused." if suspended else ""), email_status="Withdrawn", email_variant="revoked")


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
    # Section 3.2: identity first, for a renewal too (e.g. after an
    # account-recovery re-verification withdrew this authority).
    if not get_pack(db, v.country_code).parallel_identity_intake and not _identity_verified(db, v.party_id):
        raise HTTPException(status.HTTP_409_CONFLICT, REASONS["IDENTITY_REQUIRED"][0])
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

def _reopen(db: Session, query, reason_code: str, *, actor=None, correlation_id: str = "") -> int:
    """Verified records are revoked (renewable, history kept); submitted /
    in-review ones go back to the host to re-check."""
    count = 0
    for v in db.scalars(query.where(AuthorityVerification.state.in_(ACTIVE_STATES))):
        if v.state == "VERIFIED":
            _revoke(db, v, reason_code=reason_code, actor=actor, correlation_id=correlation_id)
        elif v.state in ("SUBMITTED", "MANUAL_REVIEW"):
            previous = v.state
            v.state = "ACTION_REQUIRED"
            v.reason_codes = [reason_code]
            _event(db, "AUTHORITY_ACTION_REQUIRED", v, reason_codes=[reason_code], actor=actor,
                   previous_state=previous, new_state="ACTION_REQUIRED", correlation_id=correlation_id)
            _notify(db, v, "Authority action required", describe([reason_code])["message"],
                    email_status="Action required", email_variant="action-required")
        else:
            continue
        _touch(v)
        count += 1
    return count


def reopen_for_address_change(db: Session, property_id: int, *, correlation_id: str = "") -> int:
    """ZR-PROPERTY-VERIFY-001 Section 13.3: a material address change reopens
    authority -- verified authority for the old address doesn't silently
    transfer."""
    return _reopen(db, select(AuthorityVerification).where(AuthorityVerification.property_id == property_id),
                   "PROPERTY_ADDRESS_CHANGED", correlation_id=correlation_id)


def reopen_for_ownership_change(db: Session, admin: AdminUser, property_id: int, *, correlation_id: str = "") -> int:
    """Section 9.2: a property transfer / ownership-change signal invalidates
    Owner and Agent authority before nominal expiry. A tenant's sublet
    authority rests on their tenancy, which a sale doesn't end by itself, so
    it is left for Trust & Safety to review separately."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access required")
    if db.get(Property, property_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Property not found")
    owner_or_agent = [r for r, route in ROUTE_FOR_RELATIONSHIP.items() if route in ("OWNER", "AGENT")]
    count = _reopen(db, select(AuthorityVerification).where(
        AuthorityVerification.property_id == property_id,
        AuthorityVerification.relationship_type.in_(owner_or_agent)),
        "OWNERSHIP_CHANGED", actor=admin, correlation_id=correlation_id)
    db.commit()
    return count


def reopen_for_identity_event(db: Session, party_id: int, identity_reason: str, *, correlation_id: str = "") -> int:
    """Section 13.1 (account takeover): account recovery, a legal-name change
    or invalidated identity evidence breaks the representative / owner match
    every authority assertion of this person rests on, so it must be
    re-established. Periodic identity renewal alone does not."""
    if identity_reason == "PERIODIC_RENEWAL":
        return 0
    return _reopen(db, select(AuthorityVerification).where(AuthorityVerification.party_id == party_id),
                   "IDENTITY_REVERIFICATION", correlation_id=correlation_id)


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
            _notify(db, v, "Authority expired", REASONS["AUTHORITY_EXPIRED"][0], email_status="Expired",
                    email_variant="expired")
            expired += 1
        elif expires - now <= timedelta(days=pack.expiring_soon_days) and v.expiring_notified_at is None:
            v.expiring_notified_at = now
            _event(db, "AUTHORITY_EXPIRING", v, extra={"expiresAt": expires.isoformat()})
            _notify(db, v, "Authority expiring soon",
                    f"Your authority for this property expires on {expires:%d %b %Y}. Renew it to keep listing.",
                    email_status="Expiring soon", email_variant="expiring")
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
        "auto_approval_rate": rate(sum(1 for v in submitted if v.verified_at and not v.reviewer_admin_id), len(submitted)),
        # Submitted cases waiting on property verification, OCR or the scanner.
        "waiting": db.scalar(select(func.count(AuthorityVerification.id)).where(
            AuthorityVerification.state.in_(("SUBMITTED", "MANUAL_REVIEW")))) or 0,
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
