"""authority not-official flag and country pack refresh

Revision ID: e5a2c0d9b713
Revises: d3f81b6c2a47
Create Date: 2026-10-06 15:00:00.000000

"""
import json
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a2c0d9b713'
down_revision: Union[str, None] = 'd3f81b6c2a47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The ZR-AUTHORITY-002 Section 4 requirements as of this revision, frozen here
# so the migration doesn't change if the code defaults do later.
_KEYWORDS = {
    "IN": {
        "OWNER_PROPERTY_RIGHT": ["sale deed", "conveyance deed", "gift deed", "title deed", "partition deed",
                                 "settlement deed", "encumbrance certificate", "khata", "patta", "mutation",
                                 "record of rights", "pahani", "property tax", "sub registrar", "allotment letter"],
        "OWNER_ENTITY_AUTHORITY": ["board resolution", "trust deed", "letters of administration",
                                   "authorised signatory", "authorized signatory", "probate"],
        "CO_OWNER_CONSENT": ["consent", "no objection", "noc"],
        "AGENT_MANDATE": ["power of attorney", "authority letter", "authorisation letter", "authorization letter",
                          "management agreement", "agency agreement", "mandate", "letter of authority"],
        "AGENT_ORGANIZATION_LINK": ["appointment letter", "offer letter", "employment", "authorised signatory",
                                    "authorized signatory", "employee"],
        "TENANT_OCCUPATION_RIGHT": ["rental agreement", "rent agreement", "lease agreement", "lease deed",
                                    "leave and license", "leave and licence", "tenancy agreement", "licensee"],
    },
    "GB": {
        "OWNER_PROPERTY_RIGHT": ["title register", "land registry", "title number", "transfer deed", "conveyance",
                                 "registered proprietor", "proprietorship register"],
        "OWNER_ENTITY_AUTHORITY": ["board resolution", "trust deed", "letters of administration", "grant of probate",
                                   "director"],
        "CO_OWNER_CONSENT": ["consent", "permission", "agree"],
        "AGENT_MANDATE": ["letting agreement", "management agreement", "terms of business", "agency agreement",
                          "power of attorney", "instruction to let"],
        "AGENT_ORGANIZATION_LINK": ["appointment letter", "employment", "contract of employment", "director"],
        "TENANT_OCCUPATION_RIGHT": ["tenancy agreement", "assured shorthold", "lease", "licence to occupy",
                                    "license to occupy"],
        "TENANT_SUBLET_PERMISSION": ["sublet", "sub let", "sub-let", "underlet", "sublease", "consent to sublet",
                                     "permission to sublet"],
    },
    "US": {
        "OWNER_PROPERTY_RIGHT": ["grant deed", "warranty deed", "quitclaim deed", "deed of trust", "deed",
                                 "assessor", "parcel number", "title insurance", "property tax"],
        "OWNER_ENTITY_AUTHORITY": ["resolution", "operating agreement", "trust", "certificate of incumbency",
                                   "letters testamentary"],
        "CO_OWNER_CONSENT": ["consent", "agree", "authorize"],
        "AGENT_MANDATE": ["listing agreement", "property management agreement", "management agreement",
                          "power of attorney", "exclusive right", "leasing agreement"],
        "AGENT_ORGANIZATION_LINK": ["offer letter", "employment", "independent contractor", "broker"],
        "TENANT_OCCUPATION_RIGHT": ["lease agreement", "residential lease", "rental agreement", "lease"],
        "TENANT_SUBLET_PERMISSION": ["sublet", "sublease", "sub-lease", "sub lease", "subtenant", "sub-tenant"],
    },
    "*": {
        "OWNER_PROPERTY_RIGHT": ["deed", "title", "land registry", "property tax", "ownership", "proprietor"],
        "OWNER_ENTITY_AUTHORITY": ["resolution", "trust", "administration", "authorized signatory",
                                   "authorised signatory"],
        "CO_OWNER_CONSENT": ["consent", "no objection", "agree"],
        "AGENT_MANDATE": ["power of attorney", "mandate", "management agreement", "agency agreement",
                          "letting agreement", "leasing agreement", "letter of authority", "authority letter",
                          "authorization letter", "authorisation letter"],
        "AGENT_ORGANIZATION_LINK": ["appointment", "employment", "employee", "director"],
        "TENANT_OCCUPATION_RIGHT": ["lease", "tenancy", "rental agreement", "rent agreement", "licence", "license"],
    },
}
_SUBLET = ["sublet", "sub let", "sub-let", "sublease", "sub lease", "sub-lease", "subtenant", "sub tenant",
           "sub-tenant", "underlet"]
_KEYWORDS["IN"]["TENANT_SUBLET_PERMISSION"] = _SUBLET
_KEYWORDS["*"]["TENANT_SUBLET_PERMISSION"] = _SUBLET

_EXAMPLES = {
    "OWNER_PROPERTY_RIGHT": {
        "GB": ["HM Land Registry title register / official copy", "Transfer deed (TR1)"],
        "IN": ["Sale deed", "Encumbrance certificate", "Property tax receipt with mutation record", "Khata / Patta"],
        "US": ["Recorded deed", "County assessor / tax record", "Title insurance policy"],
        "*": ["Title / land registry extract", "Deed", "Court, estate or trust record"],
    },
    "AGENT_MANDATE": {
        "IN": ["Power of attorney", "Owner's authority letter", "Property-management agreement"],
        "GB": ["Letting / management agreement", "Agent's terms of business signed by the landlord"],
        "US": ["Property management agreement", "Exclusive leasing / listing agreement"],
        "*": ["Agency / letting agreement", "Property-management agreement", "Signed owner mandate",
              "Power of attorney"],
    },
    "TENANT_OCCUPATION_RIGHT": {
        "IN": ["Registered rent / lease agreement", "Leave and licence agreement"],
        "GB": ["Assured shorthold tenancy agreement", "Lease", "Licence to occupy"],
        "US": ["Residential lease agreement"],
        "*": ["Current tenancy / lease agreement", "License to occupy"],
    },
    "TENANT_SUBLET_PERMISSION": {
        "IN": ["Landlord's NOC to sublet", "Agreement clause permitting subletting"],
        "GB": ["Landlord's written consent to sublet / underlet", "Lease clause permitting subletting"],
        "US": ["Landlord's written consent to sublease", "Lease clause permitting sublease"],
        "*": ["Landlord / agent written consent", "Lease clause permitting subletting"],
    },
}


def _refresh(requirements: dict, country: str) -> dict:
    out = {}
    for route, reqs in (requirements or {}).items():
        out[route] = []
        for req in reqs:
            rid = req.get("requirement_id")
            new = dict(req)
            new["keywords"] = list(_KEYWORDS[country].get(rid, []))
            examples = _EXAMPLES.get(rid)
            if examples:
                new["accepted_examples"] = list(examples[country])
            out[route].append(new)
    return out


def upgrade() -> None:
    """ZR-AUTHORITY-002 Section 4: a document that calls itself a sample /
    not official is flagged; the stored IN / GB / US / fallback packs get the
    country wording and accepted-document keywords as a NEW pack version
    (the previous version stays as history)."""
    op.add_column("authority_evidence", sa.Column("not_official", sa.Boolean(), nullable=False,
                                                  server_default=sa.false()))
    bind = op.get_bind()
    columns = ("country_code", "country_name", "version", "requirements", "terminology", "sublet_consent_required",
               "co_owner_consent_required", "parallel_identity_intake", "default_validity_days", "expiring_soon_days",
               "evidence_retention_days", "listing_control")
    rows = bind.execute(sa.text(
        f"SELECT id, {', '.join(columns)} FROM authority_regulatory_packs WHERE active = :t"), {"t": True}).fetchall()
    now = datetime.now(timezone.utc)
    for row in rows:
        data = dict(row._mapping)
        country = data["country_code"]
        if country not in _KEYWORDS:
            continue
        requirements = data["requirements"]
        if isinstance(requirements, str):
            requirements = json.loads(requirements)
        terminology = data["terminology"]
        if isinstance(terminology, str):
            terminology = json.loads(terminology)
        bind.execute(sa.text("UPDATE authority_regulatory_packs SET active = :f WHERE id = :i"),
                     {"f": False, "i": data["id"]})
        bind.execute(sa.text(
            "INSERT INTO authority_regulatory_packs (country_code, country_name, version, active, requirements, "
            "terminology, sublet_consent_required, co_owner_consent_required, parallel_identity_intake, "
            "default_validity_days, expiring_soon_days, evidence_retention_days, listing_control, created_at, "
            "updated_at) VALUES (:country_code, :country_name, :version, :active, CAST(:requirements AS JSON), "
            "CAST(:terminology AS JSON), "
            ":sublet_consent_required, :co_owner_consent_required, :parallel_identity_intake, :default_validity_days, "
            ":expiring_soon_days, :evidence_retention_days, :listing_control, :now, :now)"),
            {**{k: data[k] for k in columns if k not in ("version", "requirements", "terminology")},
             "version": data["version"] + 1, "active": True,
             "requirements": json.dumps(_refresh(requirements, country)), "terminology": json.dumps(terminology or {}),
             "now": now})


def downgrade() -> None:
    op.drop_column("authority_evidence", "not_official")
    # The refreshed pack versions are kept; reactivating the previous versions
    # is a Trust & Safety decision, not a schema one.
