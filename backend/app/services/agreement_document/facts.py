"""Collects every fact the Residential Occupancy Agreement template needs and
freezes it into the agreement version's snapshot (snapshot["document"]).

ZR-ENG-CLR-004 AC-27: rendering (pdf.py / text.py) reads only the frozen
snapshot, never live rows -- so anything the document shows must be
captured here, at generation time. Values are plain JSON (str/int/float/
bool/list/dict). A fact the platform genuinely doesn't have is left None /
empty and rendered as "Not specified" -- never invented."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.services.agreement_document.template_text import TEMPLATE_ID, TEMPLATE_VERSION

# Human-readable jurisdiction for "Country • region". Codes come from
# Property.jurisdiction_code; an unknown code is shown as-is.
JURISDICTION_LABELS: dict[str, str] = {
    "England": "United Kingdom • England",
    "GB-ENG": "United Kingdom • England",
    "GB-WLS": "United Kingdom • Wales",
    "GB-SCT": "United Kingdom • Scotland",
    "GB-NIR": "United Kingdom • Northern Ireland",
}

HOST_CAPACITY_LABELS: dict[str, str] = {
    "OWNER": "Owner",
    "AGENT": "Authorized Agent",
    "MANAGER": "Property Manager",
    # ZR-AUTHORITY-002 relationship types
    "CO_OWNER": "Co-owner",
    "REPRESENTATIVE": "Authorized Representative of the Owner",
    "PROPERTY_MANAGER": "Property Manager",
    "TENANT_SUBLETTER": "Tenant (Sublessor)",
}

CADENCE_LABELS: dict[str, str] = {
    "MONTHLY": "month",
    "FORTNIGHTLY": "fortnight",
    "WEEKLY": "week",
    "UPFRONT": "term (paid upfront)",
    "CUSTOM": "custom interval",
}


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(start.day, monthrange(year, month)[1]))


def fixed_term_end_date(start: date, term_months: int) -> date:
    """Last day of occupation for a fixed term: the day before the same
    calendar day term_months later (1 Oct + 12 months -> 30 Sep)."""
    return date.fromordinal(_add_months(start, term_months).toordinal() - 1)


def _humanize(code: str | None) -> str:
    return code.replace("_", " ").strip().capitalize() if code else ""


def build_document_facts(offer, latest_terms) -> dict:
    """Snapshot payload for one agreement version. Called from
    crud/leasing.py:_build_agreement_snapshot; offer must be attached to a
    session (it always is at agreement generation)."""
    db: Session = object_session(offer)
    listing = offer.listing
    room = listing.room
    prop = room.property if room else None
    guest = offer.guest
    application = offer.application

    jurisdiction_code = prop.jurisdiction_code if prop else ""
    policy = _resolve_policy(db, jurisdiction_code)
    details = dict(listing.agreement_details or {})

    host_user, host_party_id = _host_account(db, listing)
    authority = _valid_authority(db, room.id) if room else None
    renter_party_id = guest.user_account.party_id if getattr(guest, "user_account", None) else None
    host_identity = _verified_identity(db, host_party_id)
    renter_identity = _verified_identity(db, renter_party_id)

    start = latest_terms.start_date
    term_months = int(latest_terms.term_months)
    cadence = latest_terms.cadence or "MONTHLY"

    occupant_name = application.named_occupant.name if application and application.named_occupant else guest.name
    classification = room.occupancy_classification if room else None

    return {
        "template_id": TEMPLATE_ID,
        "template_version": TEMPLATE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "currency": listing.currency,
        "jurisdiction": {
            "code": jurisdiction_code,
            "label": JURISDICTION_LABELS.get(jurisdiction_code, jurisdiction_code),
        },
        "property": {
            "address": prop.address if prop else listing.location,
            "city": prop.city if prop else listing.city,
            "landmark": (prop.landmark or "") if prop else "",
            "room_identifier": f"Room {room.id}" if room else "",
            "room_description": _room_description(room, listing),
            "exclusive_use_areas": details.get("exclusive_use_areas") or _default_exclusive_areas(room),
            "shared_use_areas": details.get("shared_use_areas") or "",
            "max_occupancy": (room.max_occupants if room and room.max_occupants else listing.guests) or None,
            "classification": classification.classification if classification else "",
        },
        "host": {
            # The name on the verified identity document is the legal name;
            # the account display name is only a fallback.
            "legal_name": _legal_name(host_identity, (host_user.full_name if host_user else "") or listing.contact_name),
            "email": listing.contact_email or (host_user.email if host_user else ""),
            "phone": listing.contact_phone,
            "capacity": HOST_CAPACITY_LABELS.get((authority.relationship_type or "").upper(), "") if authority else "",
            "service_address": details.get("host_service_address") or "",
            "identity_ref": _identity_ref(host_identity),
            "authority_ref": f"AUTH-{authority.id:06d}" if authority else "",
            "property_verification_ref": _property_verification_ref(db, room.id if room else None),
        },
        "renter": {
            "legal_name": _legal_name(renter_identity, guest.name),
            "email": guest.email,
            "phone": guest.phone,
            "service_address": "The Premises",
            "identity_ref": _identity_ref(renter_identity),
            "permitted_occupants": [occupant_name] if occupant_name else [],
        },
        "rent": {
            "amount": float(latest_terms.monthly_rent),
            "cadence": cadence,
            "frequency_label": CADENCE_LABELS.get(cadence, cadence.lower()),
            "due_rule": details.get("rent_due_rule") or "",
            "payee": _payee(host_user, listing, policy),
        },
        "deposit": {
            "amount": float(latest_terms.deposit_amount),
            "treatment": _deposit_treatment(policy) if float(latest_terms.deposit_amount) > 0 else "",
        },
        "term": {
            "start_date": start.isoformat(),
            "possession_date": start.isoformat(),
            "end_date": fixed_term_end_date(start, term_months).isoformat(),
            "term_months": term_months,
            "periodic_status": f"Fixed term of {term_months} month{'s' if term_months != 1 else ''}",
            "renewal_rule": details.get("renewal_rule") or "",
        },
        "listing_fee": _listing_fee(db, listing.id),
        "utilities": details.get("utilities") or {},
        "house_rules": details.get("house_rules") or {},
        "jurisdiction_pack": _jurisdiction_pack(policy, jurisdiction_code, classification),
    }


def _resolve_policy(db: Session, jurisdiction_code: str):
    from app.crud.market_policy import resolve_market_policy

    try:
        return resolve_market_policy(db, jurisdiction_code)
    except Exception:
        # Agreement creation already enforces its own gates; a missing pack
        # here only means Schedule C shows "Not specified" rows.
        return None


def _host_account(db: Session, listing):
    from app.crud.user import get_user_by_party_id

    party_id = listing.party_id or (listing.room.property.owner_party_id if listing.room else None)
    if party_id:
        user = get_user_by_party_id(db, party_id)
        if user is not None:
            return user, party_id
    return listing.owner, party_id


def _valid_authority(db: Session, room_id: int):
    from app.crud.authority import get_valid_authority_for_room

    return get_valid_authority_for_room(db, room_id)


def _verified_identity(db: Session, party_id: int | None):
    if not party_id:
        return None
    from app.models.identity_verification import IdentityVerification

    return db.scalar(
        select(IdentityVerification)
        .where(
            IdentityVerification.party_id == party_id,
            IdentityVerification.status == "verified",
            IdentityVerification.document_category == "identity",
        )
        .order_by(IdentityVerification.verified_at.desc())
    )


def _identity_ref(record) -> str:
    return f"IDV-{record.id:06d}" if record else ""


def _legal_name(identity, fallback: str) -> str:
    extracted = (identity.extracted_name or "").strip() if identity else ""
    return extracted.title() if extracted.isupper() else (extracted or fallback)


def _property_verification_ref(db: Session, room_id: int | None) -> str:
    if not room_id:
        return ""
    from app.models.property_verification import PropertyVerification

    record = db.scalar(
        select(PropertyVerification)
        .where(PropertyVerification.room_id == room_id, PropertyVerification.status == "verified")
        .order_by(PropertyVerification.id.desc())
    )
    return f"PRV-{record.id:06d}" if record else ""


def _room_description(room, listing) -> str:
    if room is None:
        return listing.room_type or ""
    parts = [_humanize(room.room_type) or "Room"]
    if room.size:
        parts.append(f"{room.size} sq ft")
    parts.append("ensuite bathroom" if room.has_ensuite else "shared bathroom")
    return ", ".join(parts)


def _default_exclusive_areas(room) -> str:
    if room is None:
        return ""
    return "Room" + (" and ensuite" if room.has_ensuite else "")


def _payee(host_user, listing, policy) -> str:
    name = (host_user.full_name if host_user else "") or listing.contact_name or "the Host"
    if policy is not None and policy.funds_flow_profile == "DIRECT_SETTLEMENT":
        return f"{name} (Host), paid directly"
    return f"{name} (Host)"


def _deposit_treatment(policy) -> str:
    if policy is None:
        return ""
    holder = {
        "HOST_OR_AGENT": "Held by the Host or the Host's agent",
        "PROTECTION_SCHEME": "Held in a government-authorized deposit protection scheme",
    }.get(policy.deposit_custody_model, _humanize(policy.deposit_custody_model))
    parts = [holder]
    if policy.deposit_protection_deadline_days:
        parts.append(
            "protected in a government-authorised deposit protection scheme within "
            f"{policy.deposit_protection_deadline_days} days of receipt, with the prescribed information given to the Renter"
        )
    parts.append(f"returned within {policy.deposit_release_deadline_days} days of the end of occupation")
    return "; ".join(parts)


def _listing_fee(db: Session, listing_id: str) -> dict:
    from app.models.listing_fee import ListingFeePayment

    payment = db.scalar(
        select(ListingFeePayment)
        .where(ListingFeePayment.listing_id == listing_id, ListingFeePayment.status == "SUCCEEDED")
        .order_by(ListingFeePayment.id.desc())
    )
    if payment is None:
        return {"applicable": False}
    return {"applicable": True, "amount": float(payment.amount), "currency": payment.currency}


def _deposit_cap_text(monthly_multiple: float) -> str:
    """The pack stores the cap as a multiple of monthly rent; statutes usually
    state it in weeks (e.g. England: 5 weeks' rent), so show both."""
    weeks = monthly_multiple * 52 / 12
    return f"{monthly_multiple:g}× the monthly rent (about {round(weeks)} weeks' rent)"


def _jurisdiction_pack(policy, jurisdiction_code: str, classification) -> dict:
    """Schedule C rows. Only what the market policy pack actually encodes is
    stated; everything else is reported as not specified by the pack, so
    the agreement never claims a formality is (or isn't) required on the
    platform's guess."""
    from app.services.agreement_profile import DEFAULT_DISCLOSURES

    disclosures = [title for _, title, _ in DEFAULT_DISCLOSURES]
    if policy is None:
        return {"available": False, "code": jurisdiction_code, "rows": {}, "formalities_pending": False}

    compliance = list(policy.required_property_compliance_codes or [])
    rows = {
        "legal_classification": (
            _humanize(classification.classification) + " (as classified on the platform)"
            if classification and classification.classification else ""
        ),
        "rent_increase_rules": (
            f"Rent may not be changed more than once every {policy.rent_change_min_interval_days} days, "
            "and only as permitted by applicable law"
        ),
        "deposit_cap_protection": (
            f"Capped at {_deposit_cap_text(float(policy.deposit_max_rent_multiple))}"
            + (
                f"; protected in a government-authorised scheme within {policy.deposit_protection_deadline_days} days"
                if policy.deposit_protection_deadline_days else ""
            )
            + f"; returned within {policy.deposit_release_deadline_days} days of move-out"
        ),
        "licensing_registration": ", ".join(_humanize(c) for c in compliance) if compliance else "",
        "safety_documents": ", ".join(_humanize(c) for c in compliance) if compliance else "",
        "statutory_notices": (
            f"Termination notice: {policy.termination_notice_days} days; "
            f"entry notice: {policy.entry_notice_hours} hours (except lawful emergency access)"
        ),
        "right_to_rent": (
            "Required before the agreement is created"
            + (f" — {policy.occupancy_eligibility_method_note}" if policy.occupancy_eligibility_method_note else "")
            if policy.occupancy_eligibility_required else "Not required by the jurisdiction pack"
        ),
        "witnessing_notarization": "",
        "stamp_duty": "",
        "lease_registration": "",
        "consumer_disclosures": ", ".join(disclosures),
        "dispute_forum": (
            "Conciliation required before external filing"
            if policy.dispute_conciliation_requirement and policy.dispute_conciliation_requirement != "NOT_REQUIRED"
            else "Platform dispute process; does not limit access to any court, tribunal or ombudsman"
        ),
        "language": "",
        "other_terms": (
            f"Subletting: {_humanize(policy.sublet_consent_standard)} "
            f"(response within {policy.sublet_consent_response_days} days)"
        ),
    }
    return {
        "available": True,
        "code": jurisdiction_code,
        "version": int(policy.version),
        "confidence": policy.confidence,
        "rows": rows,
        # No pack field encodes an outstanding stamping/registration/witnessing
        # formality today -- when one does, set this from it (template clause
        # 40 "Production control").
        "formalities_pending": False,
    }
