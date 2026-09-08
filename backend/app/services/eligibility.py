"""ZR-ENG-CLR-001 Section 1, Rule 1 / Section 11.3: the Eligibility/Policy
Service domain.

The spec's canonical BOOKABLE(request) function (4.1) composes several
independent clauses -- PUBLICATION_ELIGIBLE(listing_version),
JURISDICTION_GATES_PASS(listing, request), INVENTORY_OPEN(...), etc. Before
this module existed, PUBLICATION_ELIGIBLE and JURISDICTION_GATES_PASS were
each independently re-derived in two places: crud/listing.py's
check_publish_eligibility (for the admin review screen) and
crud/eligibility.py's check_marketplace_standing (for the leasing/occupancy
pipeline, which deliberately *duplicated* rather than imported the listing.py
checks -- see that module's old docstring). That duplication is exactly the
architecture violation Section 1 calls out: "Zoiko Rooms must not represent
'availability' as one boolean" scattered across domains that can drift apart.

This module is now the one place either clause is computed. Listing Service
(crud/listing.py) and the leasing/occupancy pipeline (crud/eligibility.py)
both call through here; neither re-derives the logic itself.

Section 1 doesn't yet require the full INVENTORY_OPEN/HELD/BOOKED interval
machinery here (that's Rule 4 / a separate P0 item) -- this module only
covers the two clauses that were actually duplicated: publication eligibility
and jurisdiction gates.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud.authority import get_valid_authority_for_room
from app.crud.occupancy_classification import get_classification_for_room
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.room import Room


def listing_publication_eligible(listing: Listing) -> list[str]:
    """PUBLICATION_ELIGIBLE(listing_version): is this listing's own lifecycle
    state actually eligible for public/bookable exposure right now? Only
    checks the Listing Service's own domain (lifecycle state) -- room
    occupancy, jurisdiction gates, and stay rules are separate BOOKABLE()
    clauses, checked elsewhere (see is_listing_available/jurisdiction_gates_pass)."""
    reasons: list[str] = []
    if listing.state != "PUBLISHED":
        reasons.append("Listing is not currently published")
    return reasons


def jurisdiction_gates_pass(db: Session, room: Room, market_release: MarketRelease | None) -> list[str]:
    """JURISDICTION_GATES_PASS(listing, request): market release + authority
    record + occupancy classification standing for the room behind a listing.
    Re-checked at every pipeline stage from publish eligibility through
    move-in -- any of these can lapse between two stages of a 30+-night
    rental (an authority record can expire; a classification can be revoked)."""
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
