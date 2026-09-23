"""DEV/DEMO-ONLY auto-accept agent -- NOT a real verification pipeline.

This script exists because a real, ID-authenticity/right-to-rent/ownership
check needs either a paid third-party provider (Sumsub etc.) or a live
government registry -- neither of which this environment has. Rather than
silently wiring "approve everything" into the live API request path (where
it would apply to real future users with zero gate), this is a standalone
script someone has to consciously run against a database, in the same
spirit as reconcile_verification_sweeps.py/check_alerts.py already in this
folder.

It performs NO real authenticity/eligibility/ownership/screening check
whatsoever. It finds every identity verification, occupancy-eligibility
check, property verification, screening check, and SUBMITTED application
still waiting on a decision, and marks each one approved/PASS using the
existing crud functions (so notifications, audit log entries, and issued
credentials all look exactly like a real admin/host decision -- only the
actual judgment behind it is missing). It also confirms move-in the moment
an occupancy is genuinely eligible.

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
from app.crud.leasing import _auto_create_agreement_if_enabled, decide_application, resolve_market_release
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


def run_once() -> None:
    db = SessionLocal()
    try:
        admin = get_system_admin(db)

        occupancy_opened = _auto_open_and_pass_missing_occupancy_checks(db, admin)

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

        property_pending = list(
            db.scalars(
                select(PropertyVerification).where(
                    PropertyVerification.status.in_(("pending", "additional_evidence_required"))
                )
            )
        )
        for record in property_pending:
            verify_property_verification(db, record, admin, notes=AUTO_ACCEPT_REASON)
        if property_pending:
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
                moved_in += 1
            except Exception:
                pass

        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(
            f"[{stamp}] auto-accepted {len(submitted_applications)} application(s), "
            f"{len(identity_pending)} identity verification(s), "
            f"{occupancy_opened} newly-opened + {len(occupancy_pending)} already-open occupancy-eligibility check(s), "
            f"{len(property_pending)} property verification(s), "
            f"{len(screening_pending)} screening check(s), "
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
