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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.authority import get_valid_authority_for_room
from app.crud.occupancy_classification import get_classification_for_room
from app.crud.property_compliance import get_valid_property_compliance_credential
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.room import Room
from app.services.policy import get_policy
from app.services.verification_requirements import resolve_property_compliance_requirements


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

    # ZR-ENG-CLR-012 Section 14/AC-15: expired/missing mandatory property
    # compliance credentials block publication/booking the same way a
    # lapsed authority record or classification does above -- re-checked at
    # every pipeline stage this function already gates, not just at publish.
    jurisdiction_code = market_release.jurisdiction if market_release else None
    if jurisdiction_code:
        for requirement_code in resolve_property_compliance_requirements(db, jurisdiction_code):
            if get_valid_property_compliance_credential(db, room.id, requirement_code) is None:
                reasons.append(f"Missing or expired property compliance credential: {requirement_code}")

    return reasons


def failed_gate_visibility_allowed(db: Session, room: Room, market_release: MarketRelease | None) -> bool:
    """Section 14 policy key visibility.failed_gate_behavior: for a listing
    whose jurisdiction gates have started failing (an authority record
    expired, a classification was revoked, a market release was disabled --
    anything jurisdiction_gates_pass would now flag), should it still be
    publicly visible? Both platform defaults, 'hide' and 'quarantine', mean
    no here -- quarantine additionally flips the listing's own state
    elsewhere (crud/listing.py's admin-facing single-listing read); this
    function only answers the visibility question. 'visible_unbookable'
    means yes, relying on Rule 1's own booking-eligibility clauses (this same
    jurisdiction_gates_pass, re-checked at every later stage) to keep it
    unbookable regardless."""
    if not jurisdiction_gates_pass(db, room, market_release):
        return True
    return get_policy(market_release, "visibility.failed_gate_behavior") == "visible_unbookable"


# Where a host goes to fix an authority problem.
HOST_VERIFICATION_PATH = "/account/host/verification"
SUPPORT_PATH = "/account/support"


def _authority_fix(db: Session, room: Room) -> dict:
    """Why this room has no valid authority record, in the host's words, and where to fix it."""
    from app.models.authority_verification import AuthorityVerification
    from app.services.authority_service import describe, effective_state

    latest = db.scalar(
        select(AuthorityVerification)
        .where(
            AuthorityVerification.property_id == room.property_id,
            AuthorityVerification.party_id == room.property.owner_party_id,
        )
        .order_by(AuthorityVerification.id.desc())
    )
    fix = {"code": "AUTHORITY", "href": HOST_VERIFICATION_PATH, "label": "Open Verification"}
    if latest is None:
        return {**fix, "status": "NOT_STARTED", "message": (
            "You haven't completed authority verification for this property yet. Verify your authority to let it, "
            "then come back and create the agreement.")}
    state = effective_state(latest)
    if state == "EXPIRED":
        return {**fix, "status": "EXPIRED", "message": (
            "Your authority verification for this property has expired. Renew it to continue.")}
    if state == "REVOKED":
        why = describe([latest.revocation_reason_code])["message"]
        return {**fix, "status": "REVOKED", "message": (
            "Your authority verification for this property was withdrawn" + (f": {why}" if why else ".")
            + " Renew it to continue.")}
    if state == "COLLECTING":
        return {**fix, "status": "NOT_SUBMITTED", "message": (
            "You started authority verification for this property but haven't submitted it yet. Finish and submit it.")}
    if state in ("SUBMITTED", "MANUAL_REVIEW"):
        return {**fix, "status": "CHECKING", "message": (
            "Your authority verification is still being checked. You can create the agreement as soon as it's verified."),
            "label": "View progress"}
    if state == "ACTION_REQUIRED":
        why = describe(latest.reason_codes or [])["message"]
        return {**fix, "status": "ACTION_REQUIRED", "message": (
            "Your authority verification needs your attention" + (f": {why}" if why else ".")), "label": "Fix it now"}
    if state == "REJECTED":
        return {**fix, "status": "REJECTED", "message": (
            "Your authority verification for this property wasn't accepted. Start a new one with the right documents.")}
    if latest.state == "VERIFIED" and latest.room_scope_ids and room.id not in latest.room_scope_ids:
        return {**fix, "status": "ROOM_NOT_COVERED", "message": (
            "Your verified authority for this property doesn't cover this room. Add the room to it.")}
    return {**fix, "status": state, "message": "Your authority verification for this property isn't current. Review it."}


def agreement_fix_hints(db: Session, room: Room) -> list[dict]:
    """What the host can do about the authority and classification reasons that block creating an
    agreement (jurisdiction_gates_pass): each with a plain-language message and where to go."""
    hints: list[dict] = []
    if get_valid_authority_for_room(db, room.id) is None:
        hints.append(_authority_fix(db, room))
    classification = get_classification_for_room(db, room.id)
    if not classification or classification.review_state in ("UNKNOWN", "UNSUPPORTED"):
        hints.append({
            "code": "CLASSIFICATION", "status": "UNRESOLVED", "href": SUPPORT_PATH, "label": "Contact support",
            "message": "This room's occupancy classification isn't set up. Contact support and we'll sort it out.",
        })
    return hints
