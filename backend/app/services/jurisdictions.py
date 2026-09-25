"""Which regions a property can be created in, and the rules for changing a
property's region afterwards.

A property's jurisdiction_code is what every jurisdiction-aware engine
resolves its rules from (crud/market_policy.py:resolve_market_policy,
crud/listing.py:_resolve_market_release_id_for_room,
services/agreement_profile.py:resolve_agreement_profile). A region is
"open" only when an admin has configured both halves of it:

- an active MarketRelease (the market is enabled for listings), and
- a currently effective MarketPolicyPack that isn't under EMERGENCY_BLOCK
  (the rulebook deposits/fees/sublets/terminations resolve from), and
- an approved ACTIVE Listing Fee price with its billing entity and tax
  configuration (ZR-PAY-CFG-001 9.1), whenever listing_fee_fail_closed is on.

Property create/update only accepts an open region, so a host can never
pick a region that would fail later with "No market policy pack configured"
at offer, payout or termination time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.listing import Listing
from app.models.market_policy import MarketPolicyPack
from app.models.market_release import MarketRelease
from app.models.occupancy import Occupancy
from app.models.property import Property
from app.models.room import Room

# Listing states that mean the property is already being marketed or rented
# under its current region's rules. DRAFT/CHANGES_REQUESTED/REJECTED/
# WITHDRAWN/ARCHIVED listings haven't bound anyone to those rules yet.
_REGION_LOCKING_LISTING_STATES = (
    "EVIDENCE_PENDING", "REVIEW", "APPROVED", "PUBLISHED", "PAUSED", "SUSPENDED", "QUARANTINED",
)


@dataclass(frozen=True)
class OpenJurisdiction:
    code: str
    min_stay_nights: int
    market_policy_version: int
    agreements_supported: bool


def _effective_policy_pack(db: Session, code: str, as_of: date) -> MarketPolicyPack | None:
    """Same selection rule as resolve_market_policy, but returns None instead
    of raising so a region can be listed as open/closed without a 409."""
    return db.scalar(
        select(MarketPolicyPack)
        .where(
            MarketPolicyPack.jurisdiction_code == code,
            MarketPolicyPack.effective_from <= as_of,
            (MarketPolicyPack.effective_to.is_(None)) | (MarketPolicyPack.effective_to >= as_of),
        )
        .order_by(MarketPolicyPack.version.desc())
        .limit(1)
    )


def list_open_jurisdictions(db: Session, *, as_of: date | None = None) -> list[OpenJurisdiction]:
    from app.core.config import settings
    from app.crud.listing_fee import listing_fee_available
    from app.services.agreement_profile import jurisdiction_has_agreement_registry

    as_of = as_of or date.today()
    releases = db.scalars(
        select(MarketRelease).where(MarketRelease.status == "active").order_by(MarketRelease.jurisdiction)
    )
    open_regions = []
    for release in releases:
        pack = _effective_policy_pack(db, release.jurisdiction, as_of)
        if pack is None or pack.confidence == "EMERGENCY_BLOCK":
            continue
        # ZR-PAY-CFG-001 9.1 "Market activation: require active Listing Fee
        # price, billing entity mapping and approved tax configuration."
        if settings.listing_fee_fail_closed and not listing_fee_available(db, release.jurisdiction):
            continue
        open_regions.append(OpenJurisdiction(
            code=release.jurisdiction,
            min_stay_nights=release.min_stay_nights,
            market_policy_version=pack.version,
            agreements_supported=(
                not release.manual_agreement_only and jurisdiction_has_agreement_registry(db, release.jurisdiction)
            ),
        ))
    return open_regions


def normalize_jurisdiction_code(code: str) -> str:
    return (code or "").strip()


def require_open_jurisdiction(db: Session, code: str) -> str:
    """Returns the normalized code, or raises 400 naming the regions that are
    actually available -- never silently falls back to a default region."""
    code = normalize_jurisdiction_code(code)
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Select the region this property is located in")
    open_codes = [j.code for j in list_open_jurisdictions(db)]
    if code not in open_codes:
        available = ", ".join(open_codes) if open_codes else "none yet -- an admin must open a region first"
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Zoiko Rooms is not open in region '{code}'. Available regions: {available}",
        )
    return code


def property_region_is_locked(db: Session, prop: Property) -> bool:
    """True once any room in the property has an occupancy (of any status)
    or a listing past draft -- from then on deposits, notice periods and fees
    are bound to the current region's rules and must not shift underneath
    an existing renter or live listing."""
    room_ids = select(Room.id).where(Room.property_id == prop.id)
    has_occupancy = db.scalar(select(Occupancy.id).where(Occupancy.room_id.in_(room_ids)).limit(1)) is not None
    if has_occupancy:
        return True
    return db.scalar(
        select(Listing.id)
        .where(Listing.room_id.in_(room_ids), Listing.state.in_(_REGION_LOCKING_LISTING_STATES))
        .limit(1)
    ) is not None


def to_property_read(db: Session, prop: Property):
    from app.schemas.marketplace import PropertyRead

    read = PropertyRead.model_validate(prop)
    read.region_locked = property_region_is_locked(db, prop)
    return read


def apply_property_jurisdiction_change(db: Session, prop: Property, code: str) -> None:
    """Validates and applies a region change on an existing property. An
    unchanged region is always accepted, even if that region has since been
    closed -- closing a market must not make its existing properties
    uneditable."""
    code = normalize_jurisdiction_code(code)
    if code == prop.jurisdiction_code:
        return
    if property_region_is_locked(db, prop):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This property's region can't be changed because it already has a live listing or a tenancy "
            "bound to its current region's rules",
        )
    prop.jurisdiction_code = require_open_jurisdiction(db, code)
