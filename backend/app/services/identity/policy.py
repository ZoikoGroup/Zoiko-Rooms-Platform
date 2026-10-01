"""ZR-IDENTITY-001 Country Regulatory Pack lookup (Sections 1.1, 7.3, 11).

Packs live in the identity_regulatory_packs table and are the only source of
accepted documents, age rules, consent/privacy wording, retention and
re-verification policy. The rows below are only the starting data written
into an empty table (ensure_default_packs); after that the table is edited,
never this file. The wording is a neutral starting point that Legal /
Compliance must review per country before launch.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.identity_profile import DEFAULT_PACK_COUNTRY, IdentityRegulatoryPack

_GENERIC_CONSENT = (
    "If you choose a check that uses your camera, we compare a photo of you with your identity document to confirm "
    "it's you. The images are used only for identity verification and deleted under our retention policy. "
    "You can choose another verification option instead."
)
_GENERIC_PRIVACY = (
    "We use your identity information only to verify that this account belongs to you. Your documents are never "
    "shown to renters or other hosts. We keep evidence only as long as our retention policy allows, and you can ask "
    "to access or delete your data at any time from Account settings."
)

# Starting data only -- see module docstring.
_SEED_PACKS: tuple[dict, ...] = (
    {
        "country_code": DEFAULT_PACK_COUNTRY, "country_name": "Other countries",
        "accepted_document_types": ["passport", "national_id", "driving_license", "residence_permit", "government_photo_id"],
        "available_methods": ["DOCUMENT", "MANUAL"],
        "date_of_birth_required": False, "minimum_age": 18, "evidence_retention_days": 365,
    },
    {
        "country_code": "GB", "country_name": "United Kingdom",
        "accepted_document_types": ["passport", "driving_license", "national_id", "residence_permit"],
        "available_methods": ["DOCUMENT", "MANUAL"],
        "date_of_birth_required": True, "minimum_age": 18, "evidence_retention_days": 365,
    },
    {
        "country_code": "IN", "country_name": "India",
        "accepted_document_types": ["passport", "aadhaar", "pan_card", "driving_license", "voter_id"],
        "available_methods": ["DOCUMENT", "MANUAL"],
        "date_of_birth_required": True, "minimum_age": 18, "evidence_retention_days": 365,
    },
    {
        "country_code": "US", "country_name": "United States",
        "accepted_document_types": ["passport", "driving_license", "government_photo_id", "permanent_resident_card"],
        "available_methods": ["DOCUMENT", "MANUAL"],
        "date_of_birth_required": True, "minimum_age": 18, "evidence_retention_days": 365,
    },
)


def ensure_default_packs(db: Session) -> None:
    """Writes the starting packs into an empty table -- never overwrites a
    pack that already exists for a country."""
    existing = set(db.scalars(select(IdentityRegulatoryPack.country_code)))
    added = False
    for seed in _SEED_PACKS:
        if seed["country_code"] in existing:
            continue
        db.add(IdentityRegulatoryPack(
            version=1, active=True, biometric_consent_text=_GENERIC_CONSENT, privacy_notice_text=_GENERIC_PRIVACY,
            document_provider_code=settings.identity_default_provider, **seed,
        ))
        added = True
    if added:
        db.flush()


def normalize_country(code: str | None) -> str:
    return (code or "").strip().upper()


# Party.jurisdiction holds market regions ("England", "GB-SCT", "IN"), not
# ISO countries; this maps the common ones to the identity country.
_UK_REGIONS = {"ENGLAND", "SCOTLAND", "WALES", "NORTHERN IRELAND", "UK", "GB"}


def country_from_jurisdiction(jurisdiction: str | None) -> str:
    value = normalize_country(jurisdiction)
    if value in _UK_REGIONS or value.startswith("GB-"):
        return "GB"
    return value if len(value) == 2 and value.isalpha() else ""


def get_pack(db: Session, country_code: str | None) -> IdentityRegulatoryPack:
    """The country's newest active pack, or the global default pack."""
    ensure_default_packs(db)
    code = normalize_country(country_code)
    for candidate in ([code] if code else []) + [DEFAULT_PACK_COUNTRY]:
        pack = db.scalar(
            select(IdentityRegulatoryPack)
            .where(IdentityRegulatoryPack.country_code == candidate, IdentityRegulatoryPack.active.is_(True))
            .order_by(IdentityRegulatoryPack.version.desc())
        )
        if pack is not None:
            return pack
    raise LookupError("No identity regulatory pack configured")


def list_countries(db: Session) -> list[IdentityRegulatoryPack]:
    """Every country with its own active pack (the country selector)."""
    ensure_default_packs(db)
    rows = db.scalars(
        select(IdentityRegulatoryPack)
        .where(IdentityRegulatoryPack.active.is_(True), IdentityRegulatoryPack.country_code != DEFAULT_PACK_COUNTRY)
        .order_by(IdentityRegulatoryPack.country_name, IdentityRegulatoryPack.version.desc())
    )
    seen: dict[str, IdentityRegulatoryPack] = {}
    for row in rows:
        seen.setdefault(row.country_code, row)
    return list(seen.values())


def pack_summary(pack: IdentityRegulatoryPack, db: Session | None = None) -> dict:
    """What the person-facing flow needs from a pack -- including how the
    document step works (uploaded to Zoiko, or captured inside the
    provider's own flow), never any provider credential."""
    from app.services.identity.providers import UPLOAD, get_provider

    if db is not None:
        from app.services.identity.golive import resolve_provider

        provider = resolve_provider(db, pack.document_provider_code)
    else:
        provider = get_provider(pack.document_provider_code)
    return {
        "capture_mode": provider.capture_mode if provider else UPLOAD,
        "provider_available": provider is not None,
        "selfie_check": bool(provider and provider.checks_person_binding),
        "country_code": pack.country_code,
        "country_name": pack.country_name,
        "version": pack.version,
        "accepted_document_types": list(pack.accepted_document_types or []),
        "available_methods": list(pack.available_methods or []),
        "date_of_birth_required": pack.date_of_birth_required,
        "minimum_age": pack.minimum_age,
        "biometric_consent_text": pack.biometric_consent_text,
        "privacy_notice_text": pack.privacy_notice_text,
    }


def age_on(dob, today=None) -> int:
    today = today or datetime.now(timezone.utc).date()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def consent_notice_version(pack: IdentityRegulatoryPack) -> str:
    """Which notice the person saw -- recorded with their attestation."""
    return f"{pack.country_code}:v{pack.version}"


# Fields an admin may change. Any change creates a new pack version (the old
# one is kept, inactive) so recorded consent versions stay meaningful.
EDITABLE_PACK_FIELDS = (
    "country_name", "accepted_document_types", "available_methods", "date_of_birth_required", "minimum_age",
    "biometric_consent_text", "privacy_notice_text", "evidence_retention_days", "reverification_interval_days",
    "reverify_on_account_recovery", "document_provider_code", "max_attempts_per_day",
)


def new_pack_version(db: Session, pack: IdentityRegulatoryPack, changes: dict) -> IdentityRegulatoryPack:
    from datetime import datetime, timezone

    from app.models.identity_verification import IDENTITY_DOCUMENT_TYPES
    from app.models.identity_profile import VERIFICATION_METHODS
    from app.services.identity.providers import PROVIDER_CODES

    unknown = set(changes) - set(EDITABLE_PACK_FIELDS)
    if unknown:
        raise ValueError(f"Not editable: {sorted(unknown)}")
    if "accepted_document_types" in changes and not set(changes["accepted_document_types"]) <= set(IDENTITY_DOCUMENT_TYPES):
        raise ValueError("Unknown identity document type")
    if "available_methods" in changes and not set(changes["available_methods"]) <= set(VERIFICATION_METHODS):
        raise ValueError("Unknown verification method")
    if "document_provider_code" in changes and changes["document_provider_code"] not in PROVIDER_CODES:
        raise ValueError(f"document_provider_code must be one of {PROVIDER_CODES}")
    data = {f: getattr(pack, f) for f in EDITABLE_PACK_FIELDS}
    data.update(changes)
    pack.active = False
    pack.updated_at = datetime.now(timezone.utc)
    new = IdentityRegulatoryPack(country_code=pack.country_code, version=pack.version + 1, active=True, **data)
    db.add(new)
    db.flush()
    return new


def list_packs(db: Session) -> list[IdentityRegulatoryPack]:
    ensure_default_packs(db)
    return list(db.scalars(
        select(IdentityRegulatoryPack).where(IdentityRegulatoryPack.active.is_(True))
        .order_by(IdentityRegulatoryPack.country_code)
    ))
