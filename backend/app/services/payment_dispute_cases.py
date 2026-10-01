"""Links a problem reported on a rent/deposit payment record (ZR-PAY-002,
crud/rental_payment.py:report_discrepancy) to a dispute case (ZR-ENG-CLR-010),
so it appears on the renter's, host's and admin's Disputes pages with
messages, evidence and settlements.

One decision, two views:
- The money outcome stays on the payment dispute, resolved by the tenant or
  host themselves (crud/rental_payment.py:resolve_dispute_by_party:
  PAYMENT_STANDS / PAYMENT_NOT_RECEIVED / CLOSE_ONLY).
- When that is resolved, the linked claim is decided to match and the case
  closes on its own (sync_case_after_payment_resolution) -- no Zoiko Rooms
  review is needed.

Every function is best effort: a failure to open or sync the case is logged
and never undoes the payment-record action that triggered it."""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.dispute import DisputeResolutionCase, DisputeResolutionClaim
from app.models.occupancy import Occupancy
from app.models.rental_payment import RentalPaymentDispute
from app.services.dispute_forum_resolver import PAYMENT_RECORD_CLAIM_PREFIX

logger = logging.getLogger("uvicorn.error")

SOURCE_RECORD_TYPE = "RENTAL_PAYMENT_DISPUTE"

# Payment-dispute outcome -> claim status. The claim is the reporter's
# assertion that something is wrong with the payment record.
_OUTCOME_TO_CLAIM_STATUS = {
    "PAYMENT_NOT_RECEIVED": "UPHELD",   # the problem was confirmed
    "PAYMENT_STANDS": "NOT_UPHELD",     # the payment record stands as it was
    "CLOSE_ONLY": "WITHDRAWN",          # closed without a finding
}


def _occupancy_for(db: Session, obligation) -> Occupancy | None:
    if obligation.occupancy_id:
        return db.get(Occupancy, obligation.occupancy_id)
    agreement = obligation.agreement
    if agreement is not None and agreement.offer_id:
        return db.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
    return None


def _linked_claim(case: DisputeResolutionCase, dispute_id: int) -> DisputeResolutionClaim | None:
    return next(
        (c for c in case.claims if c.source_record_type == SOURCE_RECORD_TYPE and c.source_record_id == str(dispute_id)),
        None,
    )


def open_linked_case(db: Session, dispute: RentalPaymentDispute) -> DisputeResolutionCase | None:
    """Idempotent: a payment dispute gets at most one case."""
    from app.crud.disputes import open_case
    from app.models.guest import Guest
    from app.schemas.disputes import DisputeCaseCreate, DisputeClaimCreate

    if dispute.dispute_case_id:
        return db.get(DisputeResolutionCase, dispute.dispute_case_id)
    try:
        record = dispute.record
        obligation = record.obligation
        occupancy = _occupancy_for(db, obligation)
        guest = db.get(Guest, dispute.reported_by_guest_id) if dispute.reported_by_guest_id else None
        party_id = dispute.reported_by_party_id if guest is None else None

        claim = DisputeClaimCreate(
            claim_code=f"{PAYMENT_RECORD_CLAIM_PREFIX}{dispute.reason_code}",
            claim_family="PAYMENT",
            amount=float(record.declared_amount),
            currency=record.declared_currency,
            requested_remedy=(dispute.details or "")[:2000],
        )
        try:
            case = open_case(
                db, DisputeCaseCreate(occupancy_id=occupancy.id if occupancy else None, claim=claim),
                guest=guest, party_id=party_id,
            )
        except HTTPException:
            # The reporter isn't the occupancy's own renter/property owner
            # (e.g. an agent recipient) -- still open the case, unanchored.
            if occupancy is None:
                raise
            case = open_case(db, DisputeCaseCreate(occupancy_id=None, claim=claim), guest=guest, party_id=party_id)

        linked = case.claims[0]
        linked.source_record_type = SOURCE_RECORD_TYPE
        linked.source_record_id = str(dispute.id)
        linked.source_record_snapshot = {
            "payment_record_id": record.id,
            "obligation_id": obligation.id,
            "obligation_type": obligation.obligation_type,
            "reason_code": dispute.reason_code,
            "declared_amount": float(record.declared_amount),
            "declared_currency": record.declared_currency,
            "record_status_at_report": record.status,
        }
        dispute.dispute_case_id = case.id
        db.commit()
        db.refresh(case)
        return case
    except Exception:
        logger.exception("could not open a dispute case for payment dispute %s", dispute.id)
        db.rollback()
        return None


def sync_case_after_payment_resolution(
    db: Session, dispute: RentalPaymentDispute, outcome: str, notes: str = "", *,
    admin: AdminUser | None = None, guest_id: str | None = None, party_id: int | None = None,
) -> None:
    """Decides the linked claim to match the payment outcome and closes the
    case once every claim on it is resolved."""
    from app.crud.disputes import _sync_case_status_after_claim_change, close_case_when_resolved, record_dispute_decision
    from app.services.dispute_state_machine import CLAIM_TERMINAL_STATUSES, transition_claim

    if not dispute.dispute_case_id:
        return
    try:
        case = db.get(DisputeResolutionCase, dispute.dispute_case_id)
        if case is None or case.status == "CLOSED":
            return
        claim = _linked_claim(case, dispute.id)
        target = _OUTCOME_TO_CLAIM_STATUS.get(outcome)
        if claim is not None and target and claim.status not in CLAIM_TERMINAL_STATUSES:
            from datetime import datetime, timezone

            by = "Zoiko Rooms" if admin else "the tenant" if guest_id else "the host"
            if target != "WITHDRAWN" and claim.status != "INTERNAL_REVIEW":
                transition_claim(claim, "INTERNAL_REVIEW", note=f"payment-record dispute resolved by {by}")
            transition_claim(claim, target, note=f"payment-record outcome {outcome}")
            claim.outcome = target
            claim.reason_code = outcome
            claim.decided_at = datetime.now(timezone.utc)
            claim.decided_by_admin_id = admin.id if admin else None
            db.flush()
            record_dispute_decision(
                db, claim, outcome=target, basis="PAYMENT_RECORD_RESOLUTION", authority="admin" if admin else "party",
                decided_by_admin_id=admin.id if admin else None, reason_code=outcome,
            )
            _sync_case_status_after_claim_change(case)
            db.commit()

        db.refresh(case)
        close_case_when_resolved(db, case, admin=admin)
    except Exception:
        logger.exception("could not sync dispute case for payment dispute %s", dispute.id)
        db.rollback()


def link_open_payment_disputes(db: Session) -> int:
    """Backfill / safety net: opens a case for every OPEN payment dispute that
    doesn't have one yet (e.g. reported before this link existed)."""
    pending = db.scalars(
        select(RentalPaymentDispute).where(
            RentalPaymentDispute.status == "OPEN", RentalPaymentDispute.dispute_case_id.is_(None),
        )
    ).all()
    linked = 0
    for dispute in pending:
        if open_linked_case(db, dispute) is not None:
            linked += 1
    return linked
