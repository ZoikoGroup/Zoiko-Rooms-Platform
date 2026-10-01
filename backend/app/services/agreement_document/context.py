"""Resolves an agreement version into the exact strings the Residential
Occupancy Agreement prints -- one model shared by the PDF (pdf.py) and the
accessible text rendering (text.py), so both always say the same thing.

Reads the version's frozen snapshot plus the agreement's own execution
state (status, signatures). Older versions generated before
snapshot["document"] existed still render: missing facts fall back to the
snapshot's legacy top-level keys, then to NOT_SPECIFIED."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.schemas.agreement_details import UTILITY_KEYS
from app.services.agreement_document.facts import CADENCE_LABELS, fixed_term_end_date

NOT_SPECIFIED = "Not specified"

# Template clause 40 vocabulary, keyed by Agreement.status.
STATUS_LABELS: dict[str, str] = {
    "DRAFT": "Draft",
    "SENT": "Ready to Sign",
    "PARTIALLY_EXECUTED": "Partially Signed",
    "PAYMENT_IN_PROGRESS": "Fully Signed",
    "PAYMENT_PENDING": "Fully Signed",
    "SIGNED": "Executed",
    "AMENDMENT_PENDING": "Amended — Ready to Sign",
    "EXPIRED": "Expired",
    "VOID": "Cancelled/Void",
}

SIGNATURE_METHOD_LABELS: dict[str, str] = {
    "SIMPLE_ESIGN": "Simple electronic signature",
    "ADVANCED_ESIGN": "Advanced electronic signature",
    "QUALIFIED_ESIGN": "Qualified electronic signature",
    "WET_INK": "Wet-ink signature (scanned)",
}

UTILITY_LABELS: dict[str, str] = {
    "electricity": "Electricity",
    "gas_heating": "Gas / heating",
    "water_sewer": "Water / sewer",
    "internet": "Internet",
    "local_taxes": "Local / occupancy taxes",
    "building_fees": "Building / service fees",
}
UTILITY_PAYER_LABELS: dict[str, str] = {
    "HOST": "Host",
    "RENTER": "Renter",
    "SHARED": "Shared",
    "INCLUDED": "Included in rent",
    "NOT_APPLICABLE": "Not applicable",
}

# (house_rules key, Schedule B topic, legal control) in template order.
HOUSE_RULE_ROWS: tuple[tuple[str, str, str], ...] = (
    ("guests", "Guests", "Subject to mandatory law"),
    ("pets", "Pets", "Subject to mandatory law"),
    ("smoking", "Smoking / vaping", "Subject to mandatory law"),
    ("noise", "Noise / quiet hours", "Reasonable and lawful"),
    ("parking_storage", "Parking / storage", "As allocated"),
    ("shared_areas", "Shared areas", "Reasonable and lawful"),
)

# (jurisdiction_pack row key, Schedule C control label) in template order.
SCHEDULE_C_ROWS: tuple[tuple[str, str], ...] = (
    ("legal_classification", "Legal classification"),
    ("rent_increase_rules", "Rent control / increase rules"),
    ("deposit_cap_protection", "Deposit cap / protection"),
    ("licensing_registration", "Licensing / registration"),
    ("safety_documents", "Mandatory safety documents"),
    ("statutory_notices", "Required statutory notices"),
    ("right_to_rent", "Right-to-rent / immigration checks"),
    ("witnessing_notarization", "Witnessing / notarization"),
    ("stamp_duty", "Stamp duty / e-stamp"),
    ("lease_registration", "Land / lease registration"),
    ("consumer_disclosures", "Consumer / fair-housing disclosures"),
    ("dispute_forum", "Mandatory dispute forum"),
    ("language", "Language / translation"),
    ("other_terms", "Other local mandatory terms"),
)

# Schedule B move-in condition rows; recorded at move-in, not at signing.
CONDITION_ROWS: tuple[tuple[str, str], ...] = (
    ("Bedroom", "Photo/video"),
    ("Ensuite / bathroom", "Photo/video"),
    ("Furniture / appliances", "Inventory"),
    ("Keys / access devices", "Handover"),
    ("Meter readings", "Photo"),
    ("Existing damage", "Evidence"),
)
CONDITION_PENDING = "To be recorded at move-in"

# Internal signer roles -> the template's party names.
ROLE_LABELS: dict[str, str] = {"provider": "Host", "renter": "Renter", "guarantor": "Guarantor"}


@dataclass
class SignatureBlock:
    role_label: str
    legal_name: str
    capacity_label: str
    capacity: str
    method: str
    signed_at: str
    authentication: str
    reference: str


@dataclass
class DocumentModel:
    agreement_ref: str
    version_label: str
    status_label: str
    agreement_type: str
    jurisdiction: str
    footer: str
    summary_rows: list[tuple[str, str]]
    schedule_a_rows: list[tuple[str, str, str, str]]
    financial_rows: list[tuple[str, str, str, str]]
    utility_rows: list[tuple[str, str, str, str]]
    condition_rows: list[tuple[str, str, str, str]]
    house_rule_rows: list[tuple[str, str, str]]
    schedule_c_rows: list[tuple[str, str]]
    jurisdiction_pack_note: str
    signatures: list[SignatureBlock]
    certificate_rows: list[tuple[str, str]]
    generated_at: str
    extra_notes: list[str] = field(default_factory=list)


def _v(value) -> str:
    if value is None:
        return NOT_SPECIFIED
    text = str(value).strip()
    return text or NOT_SPECIFIED


def money(amount: float | None, currency: str) -> str:
    if amount is None:
        return NOT_SPECIFIED
    return f"{currency} {amount:,.2f}".strip()


def _date(iso: str | None) -> str:
    if not iso:
        return NOT_SPECIFIED
    try:
        return date.fromisoformat(iso[:10]).strftime("%d %B %Y").lstrip("0")
    except ValueError:
        return iso


def _datetime(value: datetime | None) -> str:
    return value.strftime("%d %b %Y, %H:%M UTC").lstrip("0") if value else NOT_SPECIFIED


def agreement_reference(agreement_id: int) -> str:
    return f"ZR-AGR-{agreement_id:08d}"


def build_document_model(agreement, version) -> DocumentModel:
    snap = version.snapshot or {}
    doc = snap.get("document") or {}
    currency = doc.get("currency") or snap.get("currency") or ""

    jurisdiction = doc.get("jurisdiction") or {}
    prop = doc.get("property") or {}
    host = doc.get("host") or {}
    renter = doc.get("renter") or {}
    rent = doc.get("rent") or {}
    deposit = doc.get("deposit") or {}
    term = doc.get("term") or {}
    fee = doc.get("listing_fee") or {}
    pack = doc.get("jurisdiction_pack") or {}

    agreement_ref = agreement_reference(agreement.id)
    version_label = f"{version.version_no}.0"
    jurisdiction_label = jurisdiction.get("label") or jurisdiction.get("code") or NOT_SPECIFIED

    formalities_pending = bool(pack.get("formalities_pending"))
    status_label = STATUS_LABELS.get(agreement.status, agreement.status.replace("_", " ").title())
    if agreement.status == "SIGNED" and formalities_pending:
        status_label = "Formalities Pending"

    agreement_type = _agreement_type(snap, prop)
    host_capacity = host.get("capacity") or _agent_capacity(agreement) or NOT_SPECIFIED
    renter_role = "Renter — legal role per the agreement classification in Schedule C"

    rent_amount = rent.get("amount", snap.get("monthly_rent"))
    cadence = rent.get("cadence") or "MONTHLY"
    frequency = rent.get("frequency_label") or CADENCE_LABELS.get(cadence, "month")
    rent_text = f"{money(rent_amount, currency)} per {frequency}"
    due_rule = rent.get("due_rule") or (
        "First payment due before move-in; thereafter per the payment schedule recorded on the platform"
    )
    deposit_amount = deposit.get("amount", snap.get("deposit_amount"))
    deposit_text = "None" if not deposit_amount else money(deposit_amount, currency)
    deposit_treatment = deposit.get("treatment") or (NOT_SPECIFIED if deposit_amount else "Not applicable")

    start_iso = term.get("start_date") or snap.get("start_date")
    term_months = term.get("term_months") or snap.get("term_months")
    end_iso = term.get("end_date")
    if not end_iso and start_iso and term_months:
        end_iso = fixed_term_end_date(date.fromisoformat(start_iso[:10]), int(term_months)).isoformat()
    possession_iso = term.get("possession_date") or start_iso
    periodic = term.get("periodic_status") or (f"Fixed term of {term_months} months" if term_months else NOT_SPECIFIED)

    address = _join(prop.get("address") or snap.get("listing_location"), prop.get("city") or snap.get("listing_city"))
    room_identifier = _join(prop.get("room_identifier"), prop.get("room_description"), sep=" — ")
    property_text = _join(address, room_identifier, sep=" | ")
    host_name = host.get("legal_name") or snap.get("provider_name")
    renter_name = renter.get("legal_name") or snap.get("renter_name")
    occupants = renter.get("permitted_occupants") or ([renter_name] if renter_name else [])
    max_occupancy = prop.get("max_occupancy")

    summary_rows = [
        ("Applicable jurisdiction", jurisdiction_label),
        ("Property", _v(property_text)),
        ("Host", f"{_v(host_name)}  |  Capacity: {host_capacity}"),
        ("Renter", f"{_v(renter_name)}  |  Legal role: {renter_role}"),
        ("Rent", f"{rent_text}  |  Due: {due_rule}"),
        ("Security deposit", f"{deposit_text}  |  Holder/protection: {deposit_treatment}"),
        ("Term", f"Start: {_date(start_iso)}  |  End: {_date(end_iso)}  |  Possession: {_date(possession_iso)}"),
        ("Permitted occupancy", f"{', '.join(occupants) or NOT_SPECIFIED}  |  Maximum: {_v(max_occupancy)}"),
    ]

    schedule_a_rows = [
        ("Agreement type", agreement_type, "Jurisdiction", jurisdiction_label),
        ("Property address", _v(address), "Room / unit", _v(room_identifier)),
        ("Exclusive-use areas", _v(prop.get("exclusive_use_areas")), "Shared-use areas", _v(prop.get("shared_use_areas"))),
        ("Host legal name", _v(host_name), "Host capacity", host_capacity),
        ("Host service address", _v(host.get("service_address")), "Renter legal name", _v(renter_name)),
        ("Renter service address", _v(renter.get("service_address")), "Permitted occupants", ", ".join(occupants) or "None"),
        ("Maximum occupancy", _v(max_occupancy), "Rent", rent_text),
        ("Rent due", due_rule, "Payee", _v(rent.get("payee") or host_name)),
        ("Deposit", deposit_text, "Deposit treatment", deposit_treatment),
        ("Start date", _date(start_iso), "Possession date", _date(possession_iso)),
        ("End / periodic status", f"{_date(end_iso)} — {periodic}", "Renewal", _v(term.get("renewal_rule"))),
    ]

    fee_text = money(fee.get("amount"), fee.get("currency") or currency) if fee.get("applicable") else "Not applicable"
    financial_rows = [
        ("Rent", rent_text, "Renter", "First payment due before move-in"),
        ("Security deposit", deposit_text, "Renter / Holder", deposit_treatment),
        ("Listing fee", fee_text, "Host / configured payer", "Paid to Zoiko Rooms; separate from rent"),
        ("Utilities", "See Schedule B", "As allocated", "Subject to local law"),
        ("Other lawful charges", "None", "—", "No hidden/unlisted mandatory charges"),
    ]

    utilities = doc.get("utilities") or {}
    utility_rows = []
    for key in UTILITY_KEYS:
        allocation = utilities.get(key) or {}
        payer = allocation.get("payer")
        notes = allocation.get("notes") or ("Jurisdiction rule" if key == "local_taxes" and not payer else "")
        utility_rows.append((
            UTILITY_LABELS[key],
            "Yes" if payer in ("HOST", "SHARED") else "",
            "Yes" if payer in ("RENTER", "SHARED") else "",
            _join(UTILITY_PAYER_LABELS.get(payer, "") if payer in ("INCLUDED", "NOT_APPLICABLE") else "", notes, sep=" — ")
            or (NOT_SPECIFIED if not payer else ""),
        ))

    condition_rows = [(area, CONDITION_PENDING, evidence, "") for area, evidence in CONDITION_ROWS]

    rules = doc.get("house_rules") or {}
    house_rule_rows = [(topic, _v(rules.get(key)), control) for key, topic, control in HOUSE_RULE_ROWS]

    pack_rows = pack.get("rows") or {}
    schedule_c_rows = [
        (label, pack_rows.get(key) or "Not specified by the jurisdiction pack") for key, label in SCHEDULE_C_ROWS
    ]
    if pack.get("available"):
        pack_note = f"Jurisdiction pack: {pack.get('code')} v{pack.get('version')} (status: {_humanize_status(pack.get('confidence'))})"
    else:
        pack_note = "No jurisdiction pack was resolved for this agreement version."

    signatures = _signature_blocks(agreement, version, host_name, renter_name, host_capacity)
    audit_ref = f"AUD-{agreement.id:08d}-V{version.version_no}"
    certificate_rows = [
        ("Agreement ID / version", f"{agreement_ref} / {version_label}"),
        ("Document hash / immutable copy", _document_hash_text(agreement, version, audit_ref)),
        ("Jurisdiction pack", pack_note.replace("Jurisdiction pack: ", "") if pack.get("available") else NOT_SPECIFIED),
        ("Identity / authority verification", _verification_text(host, renter)),
        ("Electronic-record consent", _consent_text(version)),
        # Mirrors Schedule C: never claims a formality is "not required" unless
        # the jurisdiction pack actually says so.
        ("Witness / notary", pack_rows.get("witnessing_notarization") or "Not specified by the jurisdiction pack"),
        ("Stamp / registration", "Pending" if formalities_pending else _join(
            pack_rows.get("stamp_duty"), pack_rows.get("lease_registration"), sep="; ",
        ) or "Not specified by the jurisdiction pack"),
        ("Audit trail / final status", f"{audit_ref}  |  {status_label.upper()}"),
    ]

    template = f"{doc.get('template_id', 'ZR-ROA-GLOBAL')} v{doc.get('template_version', '1.0')}"
    return DocumentModel(
        agreement_ref=agreement_ref,
        version_label=version_label,
        status_label=status_label,
        agreement_type=agreement_type,
        jurisdiction=jurisdiction_label,
        footer=f"ZOIKO ROOMS • {agreement_ref} • v{version_label} • {jurisdiction_label}",
        summary_rows=summary_rows,
        schedule_a_rows=schedule_a_rows,
        financial_rows=financial_rows,
        utility_rows=utility_rows,
        condition_rows=condition_rows,
        house_rule_rows=house_rule_rows,
        schedule_c_rows=schedule_c_rows,
        jurisdiction_pack_note=pack_note,
        signatures=signatures,
        certificate_rows=certificate_rows,
        generated_at=f"Template {template}",
    )


def _join(*parts, sep: str = ", ") -> str:
    return sep.join(str(p).strip() for p in parts if p and str(p).strip())


def _humanize_status(code: str | None) -> str:
    return (code or "unknown").replace("_", " ").lower()


def _agreement_type(snap: dict, prop: dict) -> str:
    base = (snap.get("agreement_class") or "room_share_agreement").replace("_", " ").capitalize()
    classification = prop.get("classification")
    if classification:
        return f"{base} ({classification.replace('_', ' ')})"
    return base


def _agent_capacity(agreement) -> str:
    provider = next((p for p in getattr(agreement, "parties", []) if p.role == "provider"), None)
    return "Authorized Agent" if provider is not None and provider.party_type == "agent" else ""


def _signature_blocks(agreement, version, host_name, renter_name, host_capacity) -> list[SignatureBlock]:
    events = {e.signer_role: e for e in (version.signature_events or [])}
    blocks = []
    for role, label, name, capacity_label, capacity, signed_at in (
        ("provider", "Host", host_name, "Capacity", host_capacity, agreement.signed_by_provider_at),
        ("renter", "Renter", renter_name, "Legal role", "Renter (see Schedule C)", agreement.signed_by_renter_at),
    ):
        event = events.get(role)
        blocks.append(SignatureBlock(
            role_label=label,
            legal_name=_v(name),
            capacity_label=capacity_label,
            capacity=capacity,
            method=SIGNATURE_METHOD_LABELS.get(event.method, event.method) if event else "Not yet signed",
            signed_at=_datetime(signed_at) if signed_at else "Not yet signed",
            authentication="Authenticated Zoiko Rooms account session" if event else "—",
            reference=f"SIG-{event.id:08d}" if event else "—",
        ))
    return blocks


def _document_hash_text(agreement, version, audit_ref: str) -> str:
    """A PDF can't contain its own hash: the executed copy is rendered first,
    then hashed and stored (crud/leasing.py:freeze_agreement_version). So the
    executed document points at where its hash is recorded instead."""
    if version.content_hash:
        return f"SHA-256 {version.content_hash}"
    if agreement.status == "SIGNED" or getattr(version, "status", "") == "EXECUTED_IMMUTABLE":
        return f"SHA-256 recorded with the immutable executed copy in the platform record ({audit_ref})"
    events = [e for e in (version.signature_events or []) if e.document_hash]
    if events:
        return f"SHA-256 of signed version {events[-1].document_hash}"
    return "Recorded when the agreement is executed"


def _verification_text(host: dict, renter: dict) -> str:
    host_refs = _join(host.get("identity_ref"), host.get("authority_ref"), host.get("property_verification_ref"))
    renter_ref = renter.get("identity_ref")
    return f"Host: {host_refs or NOT_SPECIFIED}; Renter: {renter_ref or NOT_SPECIFIED}"


def _consent_text(version) -> str:
    events = version.signature_events or []
    if not events:
        return "Captured at signing"
    return "; ".join(f"{ROLE_LABELS.get(e.signer_role, e.signer_role.capitalize())} SIG-{e.id:08d}" for e in events)
