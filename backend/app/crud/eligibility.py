"""Shared fail-closed eligibility gates for the leasing/occupancy pipeline. Mirrors
the pattern already used by crud/listing.py:check_publish_eligibility -- a pure
function returning blocking reasons, exposed as both a read-only eligibility
endpoint and enforced inside the mutating action. Deliberately duplicates (rather
than importing) listing.py's authority/classification/market checks to avoid any
regression risk on that already-shipped code path."""

from sqlalchemy import select

from app.crud.authority import get_valid_authority_for_room
from app.crud.occupancy_classification import get_classification_for_room
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.room import Room


def _resolve_market_release(db, listing: Listing) -> MarketRelease | None:
    """listing.market_release_id is resolved once, at listing-creation time (see
    crud/listing.py:_resolve_market_release_id_for_room) -- so a listing created
    before its jurisdiction's Market Release existed is stuck with a stale/null
    value forever, even after an admin later activates one. Falls back to a live
    lookup by the room's jurisdiction so that case isn't permanently blocked."""
    if listing.market_release_id:
        stored = db.get(MarketRelease, listing.market_release_id)
        if stored and stored.status == "active":
            return stored
    if listing.room is None:
        return None
    jurisdiction = listing.room.property.owner_party.jurisdiction
    return db.scalar(
        select(MarketRelease).where(MarketRelease.jurisdiction == jurisdiction, MarketRelease.status == "active")
    )


def check_marketplace_standing(db, room: Room, market_release: MarketRelease | None) -> list[str]:
    """Re-checked at both agreement-creation and move-in, since an authority record
    can expire or a classification can be revoked in the days between signing and
    move-in -- a real risk given the 30+ night minimum stay."""
    reasons: list[str] = []

    if not market_release or market_release.status != "active":
        reasons.append("No active market release for this listing")

    authority = get_valid_authority_for_room(db, room.id)
    if not authority:
        reasons.append("No verified, unexpired authority record for this room")

    classification = get_classification_for_room(db, room.id)
    if not classification or classification.review_state in ("UNKNOWN", "UNSUPPORTED"):
        reasons.append("Occupancy classification is missing or unresolved")

    return reasons


def check_offer_eligibility(db, application: Application) -> list[str]:
    reasons: list[str] = []
    listing: Listing = application.listing

    if listing.state != "PUBLISHED":
        reasons.append("Listing is not currently published")

    latest_decision = max(application.decisions, key=lambda d: d.decided_at, default=None)
    if not latest_decision or latest_decision.decision != "APPROVED":
        reasons.append("Application has not been approved")

    existing = db.query(Offer).filter(
        Offer.application_id == application.id,
        Offer.status.notin_(("DECLINED", "EXPIRED", "WITHDRAWN")),
    ).first()
    if existing:
        reasons.append("An active offer already exists for this application")

    return reasons


def check_agreement_eligibility(db, offer: Offer) -> list[str]:
    listing: Listing = offer.listing
    market_release = _resolve_market_release(db, listing)
    reasons = check_marketplace_standing(db, listing.room, market_release)

    if offer.status != "ACCEPTED":
        reasons.append("Offer has not been accepted")
    if not offer.terms:
        reasons.append("Offer has no terms")

    return reasons


def check_move_in_eligibility(db, agreement: Agreement) -> list[str]:
    offer: Offer = agreement.offer
    listing: Listing = offer.listing
    market_release = _resolve_market_release(db, listing)
    reasons = check_marketplace_standing(db, listing.room, market_release)

    if agreement.status != "SIGNED":
        reasons.append("Agreement is not signed by both parties")
    if listing.state != "PUBLISHED":
        reasons.append("Listing is not currently published")

    unpaid = [o for o in agreement.obligations if o.status not in ("PAID", "WAIVED")]
    if unpaid:
        reasons.append("Initial rent and deposit obligations are not fully paid")

    return reasons
