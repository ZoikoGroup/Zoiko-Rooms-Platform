"""ZR-ENG-CLR-004 Section 3: the fail-closed jurisdiction/arrangement
resolver. AgreementProfile = resolve(jurisdiction, property_context,
arrangement_type, host_role, occupant_role, term, regulatory_flags,
effective_date) -- a deliberately thin first profile (England, a private
room in a shared house, Zoiko-native template = form_mode A, SIMPLE_ESIGN
assurance). Every other gate is resolved per jurisdiction: the listing's
market release names the jurisdiction, and that jurisdiction needs its own
approved clause registry before agreements can be automated there.
England's placeholder registry is lazily seeded; any other jurisdiction's
registry is created by an admin (see crud/agreement_clauses.py:
copy_default_clauses_to_jurisdiction)
and approved like any other clause.

Section 3.3 'Fail closed': NO SAFE PROFILE = NO AUTOMATED CONTRACT. If this
resolver cannot determine an approved profile, callers must block agreement
generation and route to manual review -- never fall back to a generic
agreement. See crud/leasing.py:create_agreement.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agreement_clause import ClauseDefinition
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.room import Room

# The one jurisdiction whose placeholder clause registry is seeded
# automatically. Other jurisdictions are supported too, but their registry is
# never invented by code -- an admin creates it (copy_default_clauses_to_
# jurisdiction gives them England's placeholders as DRAFTs to review).
SUPPORTED_JURISDICTION = "England"
# Deliberately neutral -- NOT "tenancy" or "licence". Which legal label
# applies is exactly the kind of classification this document's own LEGAL
# CONTROL doctrine says must come from counsel, not from this code.
AGREEMENT_CLASS = "room_share_agreement"
FORM_MODE_NATIVE = "A"

# (clause_id, mandatory_level, title) -- see models/agreement_clause.py's
# module docstring: these are internal placeholders, not counsel-approved
# legal content. mandatory_level "OPTIONAL" rows here are the AC-17 approved
# optional-term catalog -- Host/admin may select from these (and only
# these) when creating an agreement; see crud/leasing.py:create_agreement's
# selected_optional_clause_ids.
DEFAULT_CLAUSES = [
    ("parties_identification", "MANDATORY", "Parties"),
    ("premises_description", "MANDATORY", "Premises"),
    ("term_and_possession", "MANDATORY", "Term"),
    ("rent_and_charges", "MANDATORY", "Rent"),
    ("deposit_terms", "MANDATORY", "Deposit"),
    ("signatures", "MANDATORY", "Signatures"),
    ("pets_and_animals_policy", "OPTIONAL", "Pets and animals"),
    ("parking_and_storage", "OPTIONAL", "Parking and storage"),
]

# (disclosure_type, title, required) -- Section 6.8/AC-15/AC-16. Same
# placeholder status as DEFAULT_CLAUSES above: generic categories a real
# jurisdiction pack would replace, not actual required-disclosure content.
DEFAULT_DISCLOSURES = [
    ("deposit_protection_information", "Deposit protection information", True),
    ("house_rules_and_safety_disclosure", "House rules and safety disclosure", True),
]

# Section 8.1 execution_profile.required_signers/signing_order. Only two
# roles are actually trackable today -- see AgreementProfile's own docstring
# and _apply_signature in crud/leasing.py for why this isn't yet extended to
# a guarantor/witness roster.
DEFAULT_REQUIRED_SIGNERS = ("provider", "renter")
DEFAULT_SIGNING_ORDER = "parallel"

# AC-10 'signature method and assurance level are resolved by execution
# policy... can vary by jurisdiction/document': a real lookup, not a bare
# literal in AgreementProfile's constructor -- today's catalog has exactly
# one entry because only one jurisdiction/class is supported at all (see
# SUPPORTED_JURISDICTION), but a second jurisdiction or document type can be
# given a different assurance level here later without touching
# resolve_agreement_profile's own logic.
ASSURANCE_LEVEL_BY_PROFILE: dict[tuple[str, str], str] = {
    (SUPPORTED_JURISDICTION, AGREEMENT_CLASS): "SIMPLE_ESIGN",
}

_PLACEHOLDER_APPROVAL_NOTE = (
    "Internal placeholder pending real counsel review -- not legal advice "
    "(see ZR-ENG-CLR-004 LEGAL CONTROL doctrine)."
)


def _resolve_assurance_level(jurisdiction: str, agreement_class: str = AGREEMENT_CLASS) -> str:
    return ASSURANCE_LEVEL_BY_PROFILE.get((jurisdiction, agreement_class), "SIMPLE_ESIGN")


@dataclass(frozen=True)
class AgreementProfile:
    agreement_class: str
    form_mode: str
    clause_ids: tuple[str, ...]
    assurance_level: str
    # AC-17: the approved optional-clause allow-list for this profile --
    # create_agreement may only accept selections that are a subset of this.
    optional_clause_ids: tuple[str, ...] = ()
    # AC-08/AC-25: clause_id -> the specific ClauseDefinition.version that was
    # actually in effect and resolved into this profile, so a generated
    # snapshot can record exactly which content version it was built from.
    clause_versions: dict[str, int] = field(default_factory=dict)
    # AC-08 'linked to template/market-pack versions': a short
    # content-addressed tag derived from the exact set of
    # (clause_id, version) pairs currently in effect -- changes whenever the
    # active clause registry changes, so two snapshots sharing this tag are
    # provably built from the same market-pack content.
    market_pack_version: str = ""
    # Section 8.1: which contractual roles must sign, and in what order.
    # Deliberately fixed to the two roles Agreement's own signed_by_*_at
    # columns can actually track -- see the module-level comment on
    # DEFAULT_REQUIRED_SIGNERS.
    required_signers: tuple[str, ...] = DEFAULT_REQUIRED_SIGNERS
    signing_order: str = DEFAULT_SIGNING_ORDER
    # AC-04: set only when form_mode is B/C/D -- the specific
    # AgreementFormTemplate this profile resolved to, so
    # crud/leasing.py:generate_agreement_pdf knows which uploaded
    # document/anchor-map/authoritative text to render against.
    form_template_id: int | None = None
    form_template_version: int | None = None
    # Which jurisdiction's clause registry this profile resolved from, so
    # callers looking up the resolved rows (e.g. crud/leasing.py's optional
    # clause catalog) never match another jurisdiction's same-named clause.
    jurisdiction: str = SUPPORTED_JURISDICTION


def ensure_default_clause_registry(db: Session) -> None:
    """Lazily seeds the placeholder clause rows the first time anything
    touches the registry -- no scheduler/seed-script dependency required,
    matching this codebase's existing self-healing patterns (e.g.
    services/booking_expiry.py's lazy expiry). Idempotent: skips any
    clause_id that already has at least one row (of any version/status),
    since a real version-2+ row for a default clause is created only through
    crud/agreement_clauses.py's admin workflow from here on, never by
    reseeding."""
    existing = {
        c.clause_id for c in db.query(ClauseDefinition).filter(
            ClauseDefinition.jurisdiction_scope == SUPPORTED_JURISDICTION,
            ClauseDefinition.agreement_class == AGREEMENT_CLASS,
        )
    }
    added = False
    today = date.today()
    for clause_id, mandatory_level, title in DEFAULT_CLAUSES:
        if clause_id in existing:
            continue
        db.add(ClauseDefinition(
            clause_id=clause_id,
            jurisdiction_scope=SUPPORTED_JURISDICTION,
            agreement_class=AGREEMENT_CLASS,
            mandatory_level=mandatory_level,
            status="APPROVED",
            version=1,
            effective_from=today,
            title=title,
            approval_note=_PLACEHOLDER_APPROVAL_NOTE,
        ))
        added = True
    if added:
        db.flush()


def _currently_effective_row(
    db: Session, clause_id: str, *, jurisdiction: str = SUPPORTED_JURISDICTION,
    agreement_class: str = AGREEMENT_CLASS, today: date | None = None,
) -> ClauseDefinition | None:
    """AC-25: the one row (if any) currently in force for this clause_id in
    this jurisdiction/class -- APPROVED, and today falls in
    [effective_from, effective_to)."""
    today = today or date.today()
    return db.scalar(
        select(ClauseDefinition)
        .where(
            ClauseDefinition.clause_id == clause_id,
            ClauseDefinition.jurisdiction_scope == jurisdiction,
            ClauseDefinition.agreement_class == agreement_class,
            ClauseDefinition.status == "APPROVED",
            ClauseDefinition.effective_from.is_not(None),
            ClauseDefinition.effective_from <= today,
            (ClauseDefinition.effective_to.is_(None)) | (ClauseDefinition.effective_to > today),
        )
        .order_by(ClauseDefinition.version.desc())
        .limit(1)
    )


def _compute_market_pack_version(clause_versions: dict[str, int]) -> str:
    tag = ",".join(f"{clause_id}:{clause_versions[clause_id]}" for clause_id in sorted(clause_versions))
    return hashlib.sha1(tag.encode()).hexdigest()[:12]


def _required_mandatory_clause_ids() -> set[str]:
    return {clause_id for clause_id, level, _ in DEFAULT_CLAUSES if level == "MANDATORY"}


def _approved_clause_rows(db: Session, jurisdiction: str, *, today: date) -> list[ClauseDefinition]:
    """Every currently-effective, non-PROHIBITED clause row for this
    jurisdiction/class -- including clauses created purely through the admin
    governance workflow (crud/agreement_clauses.py), not just DEFAULT_CLAUSES."""
    if jurisdiction == SUPPORTED_JURISDICTION:
        ensure_default_clause_registry(db)
    candidate_clause_ids = {
        row[0] for row in db.execute(
            select(ClauseDefinition.clause_id).where(
                ClauseDefinition.jurisdiction_scope == jurisdiction,
                ClauseDefinition.agreement_class == AGREEMENT_CLASS,
            ).distinct()
        )
    }
    resolved_rows = []
    for clause_id in candidate_clause_ids:
        row = _currently_effective_row(db, clause_id, jurisdiction=jurisdiction, today=today)
        if row is not None and row.mandatory_level != "PROHIBITED":
            resolved_rows.append(row)
    return resolved_rows


def jurisdiction_has_agreement_registry(db: Session, jurisdiction: str, *, today: date | None = None) -> bool:
    """True when every required MANDATORY clause has a currently-effective
    APPROVED row for this jurisdiction -- i.e. agreements can be generated
    there (subject to the market release not being manual-only)."""
    today = today or date.today()
    approved_mandatory = {
        r.clause_id for r in _approved_clause_rows(db, jurisdiction, today=today) if r.mandatory_level == "MANDATORY"
    }
    return _required_mandatory_clause_ids().issubset(approved_mandatory)


def agreement_profile_block_reason(db: Session, listing: Listing, room: Room | None) -> str | None:
    """Human-readable reason resolve_agreement_profile fails closed for this
    listing, or None when it would resolve. Same gates, same order."""
    if room is None:
        return "the listing has no room"
    market_release = db.get(MarketRelease, listing.market_release_id) if listing.market_release_id else None
    if not market_release:
        return "the listing's region has no market release"
    if market_release.status != "active":
        return f"the market for region '{market_release.jurisdiction}' is not active"
    if market_release.manual_agreement_only:
        return f"agreements in region '{market_release.jurisdiction}' are handled manually"
    if not jurisdiction_has_agreement_registry(db, market_release.jurisdiction):
        return (
            f"region '{market_release.jurisdiction}' has no approved agreement clauses yet -- "
            "an admin must add and approve them"
        )
    return None


def no_agreement_profile_message(db: Session, listing: Listing, room: Room | None) -> str:
    """The 409/eligibility text every caller shows when
    resolve_agreement_profile returns None, with the specific gate appended."""
    message = "No approved agreement profile for this listing's jurisdiction -- routed to manual review"
    reason = agreement_profile_block_reason(db, listing, room)
    return f"{message} ({reason})" if reason else message


def resolve_agreement_profile(db: Session, listing: Listing, room: Room | None) -> AgreementProfile | None:
    """Returns None (fail closed) unless every gate passes: a room exists,
    its listing has an active MarketRelease, and every clause_id this profile
    requires as MANDATORY has a currently-effective APPROVED row in that
    market's own jurisdiction registry (AC-25: not just "APPROVED" --
    APPROVED *and* within its effective window). See
    agreement_profile_block_reason for which gate failed."""
    if room is None:
        return None

    market_release = db.get(MarketRelease, listing.market_release_id) if listing.market_release_id else None
    if not market_release or market_release.status != "active":
        return None
    jurisdiction = market_release.jurisdiction

    # ZR-ENG-CLR-004 AC-04/2.3 mode E 'Manual / unsupported': a market
    # flagged manual-only fails closed here regardless of whether an
    # approved clause registry exists -- this is a legal/ops decision that
    # this jurisdiction's agreements are not safe to automate at all, not
    # merely "missing a template."
    if market_release.manual_agreement_only:
        return None

    today = date.today()
    resolved_rows = _approved_clause_rows(db, jurisdiction, today=today)

    approved_mandatory_ids = {r.clause_id for r in resolved_rows if r.mandatory_level == "MANDATORY"}
    if not _required_mandatory_clause_ids().issubset(approved_mandatory_ids):
        # A mandatory clause isn't (or is no longer) currently effective in
        # this jurisdiction -- fail closed rather than generate an agreement
        # missing required content.
        return None

    clause_versions = {r.clause_id: r.version for r in resolved_rows}
    optional_clause_ids = tuple(sorted(r.clause_id for r in resolved_rows if r.mandatory_level == "OPTIONAL"))

    # ZR-ENG-CLR-004 AC-04: form_mode is itself resolved, not a hardcoded
    # "A" literal -- a currently-effective B/C/D AgreementFormTemplate for
    # this jurisdiction/class takes priority over the native default. See
    # crud/agreement_form_templates.py for how such a template becomes
    # effective (create draft -> approve, same governance shape as clauses).
    from app.crud.agreement_form_templates import any_currently_effective_template

    form_mode = FORM_MODE_NATIVE
    form_template_id = None
    form_template_version = None
    active_template = any_currently_effective_template(db, jurisdiction, AGREEMENT_CLASS, today=today)
    if active_template is not None:
        form_mode = active_template.form_mode
        form_template_id = active_template.id
        form_template_version = active_template.version

    return AgreementProfile(
        agreement_class=AGREEMENT_CLASS,
        form_mode=form_mode,
        clause_ids=tuple(sorted(approved_mandatory_ids)),
        assurance_level=_resolve_assurance_level(jurisdiction),
        optional_clause_ids=optional_clause_ids,
        clause_versions=clause_versions,
        market_pack_version=_compute_market_pack_version(clause_versions),
        form_template_id=form_template_id,
        form_template_version=form_template_version,
        jurisdiction=jurisdiction,
    )
