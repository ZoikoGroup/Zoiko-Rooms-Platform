"""DEV/DEMO-ONLY auto-accept agent -- NOT a real verification pipeline.

This script exists because a real, ID-authenticity/right-to-rent/ownership
check needs either a paid third-party provider (Sumsub etc.) or a live
government registry -- neither of which this environment has. Rather than
silently wiring "approve everything" into the live API request path (where
it would apply to real future users with zero gate), this is a standalone
script someone has to consciously run against a database, in the same
spirit as reconcile_verification_sweeps.py/check_alerts.py already in this
folder.

It performs NO real authenticity/eligibility/ownership/screening/content
check whatsoever. It finds every identity verification, occupancy-eligibility
check, property verification, screening check, SUBMITTED application, and
listing in REVIEW still waiting on a decision, and marks each one
approved/PASS/PUBLISHED using the existing crud functions (so
notifications, audit log entries, and issued credentials all look exactly
like a real admin/host decision -- only the actual judgment behind it is
missing). It also confirms move-in the moment an occupancy is genuinely
eligible, and fills in a listing's default offer terms (from that listing's
own real price_per_night) when a host left them blank, so a decided
application doesn't stall waiting on optional fields nobody filled in.

The one thing this deliberately still never does, and will not: sign an
agreement on anyone's behalf. crud/leasing.py's own sign_agreement refuses
to let anyone but the real account holder sign for a renter/host with a
real login -- that's each party's own legal consent to a contract, not a
reviewable judgment call with no data feed like everything else this
script fast-forwards, and forging it would mean the platform binding real
people to a contract they never actually agreed to. Real payment is the
other thing left alone -- confirm_move_in only ever runs once obligations
are already PAID/WAIVED for real, this script never marks one paid itself.

NEVER point this at a database with real tenants/hosts in it. It exists
purely so a fully end-to-end demo/dev flow can run with no human clicking
"approve" and no third-party API key configured.

Run once:            python auto_accept_agent.py
Run continuously:     python auto_accept_agent.py --watch 30
                       (re-scans every 30 seconds; Ctrl+C to stop)
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.crud.activation_gate import evaluate_activation_gate, persist_activation_decision
from app.crud.identity_verification import verify_identity_verification
from app.crud.leasing import (
    _auto_create_agreement_if_enabled,
    _auto_create_offer_if_enabled,
    decide_application,
    resolve_market_release,
)
from app.crud.listing import approve_listing, publish_listing
from app.crud.occupancy import confirm_move_in
from app.crud.occupancy_eligibility import (
    get_valid_occupancy_eligibility_credential,
    list_pending_occupancy_eligibility_checks,
    open_occupancy_eligibility_check,
    record_occupancy_eligibility_result,
)
from app.crud.payment_provider import get_system_admin
from app.crud.property_verification import verify_property_verification
from app.crud.screening import list_pending_screening_checks, record_screening_decision
from app.db.session import SessionLocal
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.property_verification import PropertyVerification
from app.schemas.leasing import ApplicationDecide
from app.services.verification_requirements import resolve_verification_requirements

AUTO_ACCEPT_REASON = "DEV-ONLY auto_accept_agent.py: no real check performed, unconditional accept"


def _auto_open_and_pass_missing_occupancy_checks(db, admin) -> int:
    """Deciding an already-open OccupancyEligibilityCheck (below) isn't
    enough by itself -- nothing in the live pipeline ever opens one in the
    first place (that's normally a separate manual admin action), so an
    ACCEPTED offer whose jurisdiction requires this gate stays stuck
    forever with no check to accept. Only in this dev-only script: open one
    and immediately pass it in the same step, for every ACCEPTED offer
    that's missing a required, still-valid credential and has no
    already-open check to decide instead."""
    opened = 0
    accepted_offers = list(db.scalars(select(Offer).where(Offer.status == "ACCEPTED")))
    for offer in accepted_offers:
        if offer.agreement is not None:
            continue
        guest_user = offer.guest.user_account if offer.guest else None
        party_id = guest_user.party_id if guest_user else None
        if not party_id:
            continue
        market_release = resolve_market_release(db, offer.listing)
        jurisdiction_code = market_release.jurisdiction if market_release else None
        if not jurisdiction_code:
            continue
        for requirement in resolve_verification_requirements(db, jurisdiction_code):
            if get_valid_occupancy_eligibility_credential(db, party_id, jurisdiction_code):
                continue
            existing = [
                c for c in list_pending_occupancy_eligibility_checks(db)
                if c.party_id == party_id and c.jurisdiction_code == jurisdiction_code
            ]
            if existing:
                continue
            check = open_occupancy_eligibility_check(
                db, admin, party_id=party_id, jurisdiction_code=jurisdiction_code, method="MANUAL_DOCUMENT_CHECK",
            )
            record_occupancy_eligibility_result(
                db, check, admin, result_status="PASS", reason_note=AUTO_ACCEPT_REASON,
            )
            opened += 1
    return opened


def _auto_declare_and_verify_missing_authority(db, admin) -> int:
    """check_agreement_eligibility also requires a verified AuthorityRecord
    (the host's right to list this specific room -- owner/agent/manager)
    per room, same "genuine judgment, no real data feed" category as
    property verification, just never previously wired into this script.
    Nothing opens one automatically either (declare_authority_record is
    host self-service, verify_authority_record is a separate real admin
    decision) -- same open-then-decide shape as occupancy eligibility
    above, just for a different gate."""
    from app.models.authority_record import AuthorityRecord
    from app.crud.authority import declare_authority_record, get_valid_authority_for_room, verify_authority_record
    from app.crud.user import get_user_by_party_id

    # A room with any live-in-progress record (pending/review_required/
    # conflict, submitted for real via AuthorityRecordManager.tsx's own
    # declareHostedAuthorityRecord) already has a real human decision
    # underway -- same "additional_evidence_required excluded" principle as
    # identity/property verification above. Auto-declaring a second,
    # blindly-verified record here would out-rank it (highest id wins in
    # the UI's own display) and silently erase that real submission from
    # view. Only a room with nothing live (no record at all, or only
    # failed/expired/revoked dead ends) gets this dev-only fill-in.
    LIVE_AUTHORITY_STATUSES = ("pending", "review_required", "conflict")

    declared = 0
    for offer in db.scalars(select(Offer).where(Offer.status == "ACCEPTED")):
        if offer.agreement is not None:
            continue
        room = offer.listing.room if offer.listing else None
        if room is None or get_valid_authority_for_room(db, room.id):
            continue
        has_live_record = db.scalar(
            select(AuthorityRecord).where(
                AuthorityRecord.room_id == room.id, AuthorityRecord.status.in_(LIVE_AUTHORITY_STATUSES),
            )
        )
        if has_live_record is not None:
            continue
        host_user = get_user_by_party_id(db, room.property.owner_party_id)
        if host_user is None:
            continue
        try:
            record = declare_authority_record(
                db, host_user, room, relationship_type="OWNER", evidence_ref=AUTO_ACCEPT_REASON,
            )
            verify_authority_record(db, record, admin)
            declared += 1
        except Exception:
            pass
    return declared


def _auto_classify_missing_occupancy(db) -> int:
    """check_agreement_eligibility also requires a room's OccupancyClassification
    to be APPROVED (models/occupancy_classification.py's own REVIEW_STATES:
    "a room can never publish while classification is UNKNOWN or
    UNSUPPORTED"). Unlike authority/property/identity, set_classification
    has no real admin-role check at all in this codebase -- it's already
    the most mechanical of these gates, just never auto-filled before."""
    from app.crud.occupancy_classification import get_classification_for_room, set_classification
    from app.schemas.marketplace import OccupancyClassificationSet

    classified = 0
    for offer in db.scalars(select(Offer).where(Offer.status == "ACCEPTED")):
        if offer.agreement is not None:
            continue
        room = offer.listing.room if offer.listing else None
        if room is None:
            continue
        existing = get_classification_for_room(db, room.id)
        if existing is not None and existing.review_state == "APPROVED":
            continue
        set_classification(
            db, room,
            OccupancyClassificationSet(
                classification="shared_residential_room", confidence=1.0,
                evidence_ref=AUTO_ACCEPT_REASON, review_state="APPROVED",
            ),
        )
        classified += 1
    return classified


def run_once() -> None:
    db = SessionLocal()
    try:
        admin = get_system_admin(db)

        occupancy_opened = _auto_open_and_pass_missing_occupancy_checks(db, admin)
        authority_declared = _auto_declare_and_verify_missing_authority(db, admin)
        occupancy_classified = _auto_classify_missing_occupancy(db)

        # The actual who-do-we-rent-to decision -- approved unconditionally
        # here per explicit instruction, using the same real crud function
        # a host's own click uses, so it synchronously triggers whatever
        # offer auto-creation is already configured for this listing's
        # market release, same as a real approval would.
        submitted_applications = list(db.scalars(select(Application).where(Application.status == "SUBMITTED")))
        for application in submitted_applications:
            decide_application(db, application, admin, ApplicationDecide(decision="APPROVED", note=AUTO_ACCEPT_REASON))
        if submitted_applications:
            db.commit()

        # Retry offer creation for any already-DECIDED application still
        # missing one -- _auto_create_offer_if_enabled only ever runs once,
        # synchronously, at the moment of decision (see decide_application
        # above); if that one attempt failed for a transient reason (e.g. the
        # market release wasn't resolvable yet), nothing before this retried
        # it. Same shape as the agreement retry loop further down.
        #
        # _auto_create_offer_if_enabled also silently no-ops when the listing
        # has no default_monthly_rent/default_deposit_amount/default_term_months
        # set -- a real gap, since the host's own "List a Room" form treats
        # that as optional. Unlike payment/signing/handover (claims that a
        # real-world event already happened), a listing's own asking price is
        # forward-looking business configuration with no objective right
        # answer -- same category as every other review decision this script
        # already makes. Derived from the listing's own real, host-set
        # price_per_night (never a number invented from nothing): monthly
        # rent == price_per_night, deposit == one month's rent, term == 11
        # months, matching every other real listing already on this platform.
        for application in db.scalars(select(Application).where(Application.status == "DECIDED")):
            listing = application.listing
            if listing is None or application.offer is not None:
                continue
            if listing.default_monthly_rent is None:
                listing.default_monthly_rent = listing.price_per_night
            if listing.default_deposit_amount is None:
                listing.default_deposit_amount = listing.price_per_night
            if listing.default_term_months is None:
                listing.default_term_months = 11
            db.commit()

        retried_offers = 0
        for application in db.scalars(select(Application).where(Application.status == "DECIDED")):
            if application.offer is not None:
                continue
            _auto_create_offer_if_enabled(db, application, admin)
            db.refresh(application)
            if application.offer is not None:
                retried_offers += 1

        # "additional_evidence_required" deliberately excluded -- that status
        # now means a REAL check (services/document_ocr.py) or a real admin
        # genuinely flagged this submission as unreadable/insufficient and
        # is waiting on a fresh re-upload. Auto-verifying it anyway would
        # silently erase that real signal -- the one thing this whole
        # script is supposed to never do. Only a genuinely fresh
        # submission (back to "pending") should ever reach this step.
        identity_pending = list(
            db.scalars(select(IdentityVerification).where(IdentityVerification.status == "pending"))
        )
        for record in identity_pending:
            verify_identity_verification(db, record, admin, notes=AUTO_ACCEPT_REASON)
        if identity_pending:
            db.commit()

        occupancy_pending = list_pending_occupancy_eligibility_checks(db)
        for check in occupancy_pending:
            record_occupancy_eligibility_result(
                db, check, admin, result_status="PASS", reason_note=AUTO_ACCEPT_REASON,
            )
        if occupancy_pending:
            db.commit()

        # Same exclusion as identity_pending above, for the same reason:
        # "additional_evidence_required" now means declare_property_verification's
        # own real OCR address-match check (services/document_ocr.py) genuinely
        # looked and didn't find this room's registered address in the
        # document. Auto-verifying it anyway would silently erase that real
        # rejection -- exactly what this script must never do. Only a
        # genuinely fresh "pending" submission (OCR unavailable/inconclusive,
        # fails open) should ever reach this step.
        property_pending = list(
            db.scalars(select(PropertyVerification).where(PropertyVerification.status == "pending"))
        )
        for record in property_pending:
            verify_property_verification(db, record, admin, notes=AUTO_ACCEPT_REASON)
        if property_pending:
            db.commit()

        listings_pending = list(db.scalars(select(Listing).where(Listing.state == "REVIEW")))
        for listing in listings_pending:
            approve_listing(db, listing, admin)
            publish_listing(db, listing, admin)
        if listings_pending:
            db.commit()

        screening_pending = list_pending_screening_checks(db)
        for check in screening_pending:
            record_screening_decision(db, check, admin, decision_status="PASS", decision_reason=AUTO_ACCEPT_REASON)
        if screening_pending:
            db.commit()

        # Identity and occupancy-eligibility gates above may have just
        # cleared for an ACCEPTED offer whose agreement auto-creation
        # already tried and failed once at acceptance time (see
        # user_accept_offer/_auto_create_agreement_if_enabled in
        # crud/leasing.py) -- retry using the exact same real function, so
        # the agreement now gets created the moment every gate is clear,
        # same policy check and eligibility enforcement as the live path.
        retried_agreements = 0
        for offer in db.scalars(select(Offer).where(Offer.status == "ACCEPTED")):
            if offer.agreement is not None:
                continue
            _auto_create_agreement_if_enabled(db, offer)
            db.refresh(offer)
            if offer.agreement is not None:
                retried_agreements += 1

        # Never touches signing, payment, or the three handover events
        # (HANDOVER_READY/POSSESSION_DELIVERED/RENTER_RECEIPT) -- all four
        # are a real party's own attestation of something that actually
        # happened in the physical world, not a reviewable judgment call
        # with no data feed like everything else this script fast-forwards.
        # record_handover_event's own docstring calls each one "immutable
        # evidence" -- faking HANDOVER_READY/POSSESSION_DELIVERED would mean
        # this script claiming a host handed over keys that were never
        # handed over, and RENTER_RECEIPT is explicitly the renter's own
        # account action (api/routes/user_rentals.py:submit_handover_receipt).
        # Uses the exact same evaluate_activation_gate -> confirm_move_in
        # sequence as the real POST .../confirm-move-in route (not the
        # older, narrower check_move_in_eligibility check alone), so this
        # correctly waits on the handover events same as a real click would
        # -- an earlier version of this script skipped that gate entirely.
        moved_in = 0
        for occupancy in db.scalars(select(Occupancy).where(Occupancy.status == "PENDING_MOVE_IN")):
            agreement = db.scalar(select(Agreement).where(Agreement.offer_id == occupancy.offer_id))
            if agreement is None:
                continue
            evaluation = evaluate_activation_gate(db, occupancy)
            persist_activation_decision(db, occupancy, evaluation, trigger="auto_accept_agent", admin=admin)
            db.commit()
            if evaluation.outcome != "ACTIVATE":
                continue
            try:
                confirm_move_in(db, agreement, admin)
                # confirm_move_in deliberately never commits itself (its own
                # docstring: "The route commits this state transition
                # together with its persisted activation decision, audit
                # row, and outbox events") -- every real caller is a route
                # that commits right after. This script isn't a route, so
                # without this the state change silently never persisted:
                # every cycle re-evaluated ACTIVATE, re-ran confirm_move_in,
                # and lost the result again on session close.
                db.commit()
                moved_in += 1
            except Exception:
                db.rollback()

        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(
            f"[{stamp}] auto-accepted {len(submitted_applications)} application(s), "
            f"{retried_offers} offer(s) unblocked and created, "
            f"{len(identity_pending)} identity verification(s), "
            f"{occupancy_opened} newly-opened + {len(occupancy_pending)} already-open occupancy-eligibility check(s), "
            f"{authority_declared} authority record(s) declared+verified, "
            f"{occupancy_classified} room(s) classified, "
            f"{len(property_pending)} property verification(s), "
            f"{len(screening_pending)} screening check(s), "
            f"{len(listings_pending)} listing(s) approved+published, "
            f"{moved_in} move-in(s) confirmed, "
            f"{retried_agreements} agreement(s) unblocked and created"
        )
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--watch", type=int, default=0, metavar="SECONDS",
        help="re-scan and re-accept every SECONDS (default: run once and exit)",
    )
    args = parser.parse_args()

    if args.watch <= 0:
        run_once()
    else:
        print(f"Watching every {args.watch}s -- Ctrl+C to stop.")
        while True:
            run_once()
            time.sleep(args.watch)
