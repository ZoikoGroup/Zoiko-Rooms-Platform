"""Shared fail-closed eligibility gates for the leasing/occupancy pipeline. Mirrors
the pattern already used by crud/listing.py:check_publish_eligibility -- a pure
function returning blocking reasons, exposed as both a read-only eligibility
endpoint and enforced inside the mutating action. Deliberately duplicates (rather
than importing) listing.py's authority/classification/market checks to avoid any
regression risk on that already-shipped code path."""

from sqlalchemy import func, select

from app.crud.authority import get_valid_authority_for_room
from app.crud.occupancy_classification import get_classification_for_room
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy import Occupancy
from app.models.room import Room


def check_room_capacity(db, room: Room, *, exclude_occupancy_id: int | None = None) -> list[str]:
    """Blocks move-in once a room already holds max_occupants live tenancies.
    Previously nothing checked this at all -- two independent tenancies could
    silently double-book the same room. exclude_occupancy_id lets a re-check on
    an occupancy already counted (e.g. re-running eligibility) skip itself."""
    query = select(func.count()).select_from(Occupancy).where(
        Occupancy.room_id == room.id,
        Occupancy.status.in_(("PENDING_MOVE_IN", "ACTIVE")),
    )
    if exclude_occupancy_id is not None:
        query = query.where(Occupancy.id != exclude_occupancy_id)
    current_occupants = db.scalar(query) or 0
    if current_occupants >= room.max_occupants:
        return [f"Room is already at capacity ({current_occupants}/{room.max_occupants} occupants)"]
    return []


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
    market_release = db.get(MarketRelease, listing.market_release_id) if listing.market_release_id else None
    reasons = check_marketplace_standing(db, listing.room, market_release)

    if offer.status != "ACCEPTED":
        reasons.append("Offer has not been accepted")
    if not offer.terms:
        reasons.append("Offer has no terms")

    return reasons


def check_move_in_eligibility(db, agreement: Agreement) -> list[str]:
    offer: Offer = agreement.offer
    listing: Listing = offer.listing
    market_release = db.get(MarketRelease, listing.market_release_id) if listing.market_release_id else None
    reasons = check_marketplace_standing(db, listing.room, market_release)

    if agreement.status != "SIGNED":
        reasons.append("Agreement is not signed by both parties")
    if listing.state != "PUBLISHED":
        reasons.append("Listing is not currently published")

    unpaid = [o for o in agreement.obligations if o.status not in ("PAID", "WAIVED")]
    if unpaid:
        reasons.append("Initial rent and deposit obligations are not fully paid")

    reasons += check_room_capacity(db, listing.room)

    return reasons
