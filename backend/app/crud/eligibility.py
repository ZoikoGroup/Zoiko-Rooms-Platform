"""Shared fail-closed eligibility gates for the leasing/occupancy pipeline. Mirrors
the pattern already used by crud/listing.py:check_publish_eligibility -- a pure
function returning blocking reasons, exposed as both a read-only eligibility
endpoint and enforced inside the mutating action.

ZR-ENG-CLR-001 Section 11.3: publication eligibility and jurisdiction-gate
standing are computed by the one Eligibility/Policy Service module
(app/services/eligibility.py), not re-derived here -- this file used to
deliberately *duplicate* those checks to avoid regression risk on the
already-shipped crud/listing.py code path, but that duplication is exactly
the cross-domain drift risk Section 1 calls out."""

from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.room import Room
from app.services.eligibility import jurisdiction_gates_pass, listing_publication_eligible


def check_marketplace_standing(db, room: Room, market_release: MarketRelease | None) -> list[str]:
    """Thin compatibility wrapper over the Eligibility/Policy Service -- kept
    under its established name since it's this module's own public API."""
    return jurisdiction_gates_pass(db, room, market_release)


def check_offer_eligibility(db, application: Application) -> list[str]:
    reasons: list[str] = []
    listing: Listing = application.listing

    reasons.extend(listing_publication_eligible(listing))

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
    reasons.extend(listing_publication_eligible(listing))

    unpaid = [o for o in agreement.obligations if o.status not in ("PAID", "WAIVED")]
    if unpaid:
        reasons.append("Initial rent and deposit obligations are not fully paid")

    return reasons
