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

from sqlalchemy import func, select
from app.crud.occupancy_eligibility import get_valid_occupancy_eligibility_credential
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy import Occupancy
from app.models.room import Room
from app.services.agreement_effectiveness import is_agreement_effective
from app.services.agreement_profile import resolve_agreement_profile
from app.services.eligibility import jurisdiction_gates_pass, listing_publication_eligible
from app.services.verification_requirements import resolve_verification_requirements


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


def _check_occupancy_eligibility_requirements(db, listing: Listing, guest) -> list[str]:
    """ZR-ENG-CLR-012 Section 9/AC-04/AC-05: shared by both
    check_agreement_eligibility (the 'CONFIRMED booking' gate) and
    check_move_in_eligibility (the 'occupancy activation' gate) below --
    the doc requires BOTH stages to re-check this, not just the first one a
    credential could have been revoked or expired between them. Resolves
    to nothing for jurisdictions with no verification policy configured
    (see resolve_verification_requirements), so it never blocks existing
    flows unless a jurisdiction has actually opted in."""
    market_release = db.get(MarketRelease, listing.market_release_id) if listing.market_release_id else None
    jurisdiction_code = market_release.jurisdiction if market_release else None
    party_id = guest.user_account.party_id if guest and guest.user_account else None
    if not (jurisdiction_code and party_id):
        return []

    reasons: list[str] = []
    for requirement in resolve_verification_requirements(db, jurisdiction_code):
        credential = get_valid_occupancy_eligibility_credential(db, party_id, jurisdiction_code)
        if credential is None:
            reasons.append(
                f"{requirement.requirement_code} check has not been completed for this jurisdiction "
                "-- routed to manual review"
            )
    return reasons


def check_agreement_eligibility(db, offer: Offer) -> list[str]:
    listing: Listing = offer.listing
    market_release = db.get(MarketRelease, listing.market_release_id) if listing.market_release_id else None
    reasons = check_marketplace_standing(db, listing.room, market_release)

    if offer.status != "ACCEPTED":
        reasons.append("Offer has not been accepted")
    if not offer.terms:
        reasons.append("Offer has no terms")

    # ZR-ENG-CLR-004 Section 3.3: create_agreement itself fails closed here
    # (resolve_agreement_profile is None -> 409) -- this pre-check used to
    # say "eligible: true" right up until that real call failed, since it
    # never actually resolved a profile. Same reason text as the real 409.
    if resolve_agreement_profile(db, listing, listing.room) is None:
        reasons.append("No approved agreement profile for this listing's jurisdiction -- routed to manual review")

    # ZR-ENG-CLR-012 AC-04: "CONFIRMED booking cannot occur while a mandatory
    # confirmation-stage verification requirement is unresolved."
    reasons += _check_occupancy_eligibility_requirements(db, listing, offer.guest)

    return reasons


def check_move_in_eligibility(db, agreement: Agreement) -> list[str]:
    offer: Offer = agreement.offer
    listing: Listing = offer.listing
    market_release = db.get(MarketRelease, listing.market_release_id) if listing.market_release_id else None
    reasons = check_marketplace_standing(db, listing.room, market_release)

    # ZR-ENG-CLR-004 AC-13/AC-14: Executed and Effective are separate states
    # -- move-in requires the agreement to actually be effective (signed AND
    # its own lease start_date reached), not merely "last signature
    # received" (see services/agreement_effectiveness.py).
    if agreement.status != "SIGNED":
        reasons.append("Agreement is not signed by both parties")
    elif not is_agreement_effective(agreement):
        reasons.append("Agreement is signed but not yet effective (lease start date not reached)")
    reasons.extend(listing_publication_eligible(listing))

    unpaid = [o for o in agreement.obligations if o.status not in ("PAID", "WAIVED")]
    if unpaid:
        reasons.append("Initial rent and deposit obligations are not fully paid")

    # The occupancy this very check is gating (created PENDING_MOVE_IN at
    # signing -- see crud/leasing.py's own _apply_signature) already counts
    # itself as a live occupant of the room; exclude it so a room's own
    # first tenant doesn't fail its own capacity check.
    this_occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    reasons += check_room_capacity(db, listing.room, exclude_occupancy_id=this_occupancy.id if this_occupancy else None)

    # ZR-ENG-CLR-012 AC-05: "Occupancy cannot activate while a mandatory
    # occupancy-stage verification requirement is unresolved." A credential
    # valid at agreement-confirmation time could have since expired or been
    # revoked (e.g. FAIL_INELIGIBLE re-check, or simple time-limited
    # expiry) -- re-checked here independently, not assumed still valid
    # from check_agreement_eligibility's earlier pass.
    reasons += _check_occupancy_eligibility_requirements(db, listing, offer.guest)

    return reasons
