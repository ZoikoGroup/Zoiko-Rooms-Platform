"""Listing versioning + material-change classification (ZR-ENG-CLR-001
Section 1, Rule 3 / Section 6.1).

Deterministic and model-outside: every Listing content edit produces a new
immutable ListingVersion snapshot. Whether that snapshot is MATERIAL decides
whether it can silently replace the current public version (non-material) or
must go through review first (material) -- see crud/listing.py.
"""

from __future__ import annotations

import hashlib
import json

# Field -> material classification, per the spec's 6.1 table. Fields not
# listed here (rating/review_count/state/published_at/rejection_reason/etc.)
# are either not part of the versioned content snapshot or are live
# operational fields the spec doesn't classify as listing "content" at all.
MATERIAL_FIELDS = {
    # Address/property identity + accommodation/room type or capacity
    "room_id", "room_type", "guests", "bedrooms", "bathrooms", "size",
    # Host authority/ownership basis -- which room backs this listing at all
    "property_type",
    # Rent/mandatory charges/pricing structure (spec: "Material or
    # policy-controlled" -- until a pricing ruleset exists, always material)
    "price_per_night", "currency",
    # Material house rules/restrictions
    "min_stay_nights",
}

# Fields the spec explicitly calls non-material (grammar/description polish,
# photo changes are moderation-sensitive but not re-approval-gated here).
NON_MATERIAL_FIELDS = {
    "name", "city", "location", "latitude", "longitude", "description",
    "images", "amenities", "tags", "featured",
    "contact_name", "contact_phone", "contact_email",
}

# Every content field that participates in a version snapshot at all.
VERSIONED_FIELDS = MATERIAL_FIELDS | NON_MATERIAL_FIELDS


def build_snapshot(listing) -> dict:
    """Canonical content snapshot of a Listing at this instant. Only
    VERSIONED_FIELDS are included -- operational fields (state, rating,
    published_at, owner_id, party_id, ...) are never part of a version;
    they're live Listing-row facts, not approved content."""
    return {field: getattr(listing, field) for field in sorted(VERSIONED_FIELDS)}


def compute_content_hash(snapshot: dict) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_material_change(previous: dict | None, current: dict) -> tuple[dict[str, bool], bool]:
    """Returns (material_change_flags, is_material) comparing `current`
    against `previous` (None for a listing's very first version -- which is
    never itself "material", there's nothing to compare against)."""
    if previous is None:
        return {field: False for field in VERSIONED_FIELDS}, False

    flags: dict[str, bool] = {}
    is_material = False
    for field in VERSIONED_FIELDS:
        changed = previous.get(field) != current.get(field)
        flags[field] = changed
        if changed and field in MATERIAL_FIELDS:
            is_material = True
    return flags, is_material
