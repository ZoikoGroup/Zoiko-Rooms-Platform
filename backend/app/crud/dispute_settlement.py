"""ZR-ENG-CLR-010 Section 4/13 (P1)/22/23: bilateral settlement negotiation.

NON-BEHAVIOR: like crud/dispute_external_proceeding.py, this never touches
DepositRecord, DepositClaim, Obligation, RefundRequest or PayoutRecord --
an EFFECTIVE settlement resolves the linked claim's status/outcome, it
never itself executes a refund/payout/deposit release. Downstream money
movement (if the settlement's terms require it) stays a separate, manual
Section 5/6/7 action for now -- automating that handoff is later-phase work."""

import hashlib
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.disputes import _resolve_market_policy_for_occupancy, _sync_case_status_after_claim_change, record_dispute_decision
from app.crud import notification as notif_crud
from app.models.dispute import DisputeResolutionCase, DisputeResolutionClaim
from app.models.dispute_settlement import DisputeSettlement, DisputeSettlementClaimLink
from app.models.guest import Guest
from app.models.occupancy import Occupancy
from app.models.property import Property
from app.services.dispute_state_machine import transition_claim, transition_settlement

_SETTLEABLE_AUTHORITY_CLASSES = ("A1", "A2")


def _terms_hash(terms_text: str, amount: float | None, currency: str, claim_ids: list[int]) -> str:
    raw = f"{terms_text}|{amount}|{currency}|{sorted(claim_ids)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _assert_no_termination_double_recovery(claim: DisputeResolutionClaim) -> None:
    """AC-36: "Cancellation/termination disputes preserve Section 6/7
    policy snapshots and no-double-recovery logic." A CANCELLATION/
    EARLY_TERMINATION claim (see crud/disputes.py:_resolve_source_record)
    links to the occupancy's TerminationCase; if that case's termination
    engine has already EXECUTED a refund entitlement, a settlement here
    would grant a second, independent payout for the same terminated
    occupancy. Only EXECUTED blocks -- a merely CALCULATED/APPROVED
    entitlement hasn't paid anything out yet, so there's nothing to double
    yet."""
    if claim.source_record_type != "TERMINATION_CASE":
        return
    if claim.source_record_snapshot.get("refund_entitlement_status") == "EXECUTED":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Claim {claim.id}: this occupancy's termination refund entitlement has already been executed -- "
            "cannot settle for additional monetary relief",
        )


def _assert_not_non_waivable(db: Session, case: DisputeResolutionCase, claim: DisputeResolutionClaim) -> None:
    """AC-28: "A settlement cannot silently waive non-waivable rights
    where the market pack prohibits that result." Previously enforced
    only by acknowledges_no_nonwaivable_waiver -- a self-certified
    checkbox with no market-configured content behind it (this model's
    own docstring used to admit as much). A market pack that actually
    lists claim.claim_family in dispute_non_waivable_claim_families now
    makes this a real, structural refusal instead of only an attestation;
    a market with nothing configured (the default, matching "no universal
    arbitration"-style honesty) blocks nothing here."""
    if case.occupancy is None:
        return
    market_policy_pack = _resolve_market_policy_for_occupancy(db, case.occupancy)
    if market_policy_pack is None:
        return
    if claim.claim_family in market_policy_pack.dispute_non_waivable_claim_families:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Claim {claim.id}: this market pack prohibits settling a {claim.claim_family} claim -- "
            "it cannot be waived through bilateral settlement",
        )


def _claims_for_settlement(db: Session, case: DisputeResolutionCase, claim_ids: list[int]) -> list[DisputeResolutionClaim]:
    if not claim_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A settlement must be linked to at least one claim")
    claims: list[DisputeResolutionClaim] = []
    for claim_id in claim_ids:
        claim = db.get(DisputeResolutionClaim, claim_id)
        if not claim or claim.case_id != case.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Claim {claim_id} does not belong to this case")
        if claim.authority_class not in _SETTLEABLE_AUTHORITY_CLASSES:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Claim {claim_id} (authority class {claim.authority_class}) cannot be settled bilaterally",
            )
        _assert_no_termination_double_recovery(claim)
        _assert_not_non_waivable(db, case, claim)
        claims.append(claim)
    return claims


def _notify_settlement(db: Session, case: DisputeResolutionCase, *, proposed_by_role: str, title: str, message: str) -> None:
    # Notify whichever side did NOT propose -- mirrors crud/disputes.py's
    # _notify_case_opened shape.
    if proposed_by_role == "RENTER" and case.property_id:
        prop = db.get(Property, case.property_id)
        if prop:
            notif_crud.notify_user_by_party(db, prop.owner_party_id, title=title, message=message, notification_type="dispute_settlement.event")
    if proposed_by_role == "HOST" and case.occupancy_id:
        occ = db.get(Occupancy, case.occupancy_id)
        if occ and occ.guest:
            notif_crud.notify_user_by_guest(db, occ.guest, title=title, message=message, notification_type="dispute_settlement.event")


def propose_settlement(
    db: Session,
    case: DisputeResolutionCase,
    *,
    guest: Guest | None = None,
    party_id: int | None = None,
    claim_ids: list[int],
    terms_text: str,
    amount: float | None = None,
    currency: str | None = None,
    expires_at: datetime | None = None,
    acknowledges_no_nonwaivable_waiver: bool,
    supersedes: DisputeSettlement | None = None,
) -> DisputeSettlement:
    if (guest is None) == (party_id is None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A settlement must be proposed by exactly one of a renter or a host")

    # ZR-ENG-CLR-005 12.3 'Contractual obligation is denominated in the
    # agreement currency': resolve from the case's occupancy/listing when one
    # is linked (the common shape); "INR" is a last-resort fallback only for
    # the claim families that never attach an occupancy at all (see
    # DisputeResolutionCase's own docstring on occupancy_id/property_id).
    if currency is None:
        currency = case.occupancy.listing.currency if case.occupancy else "INR"
    if not acknowledges_no_nonwaivable_waiver:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "acknowledges_no_nonwaivable_waiver must be explicitly confirmed -- this settlement cannot waive non-waivable rights",
        )

    claims = _claims_for_settlement(db, case, claim_ids)
    proposed_by_role = "RENTER" if guest is not None else "HOST"

    settlement = DisputeSettlement(
        case_id=case.id,
        proposed_by_role=proposed_by_role,
        proposed_by_guest_id=guest.id if guest is not None else None,
        proposed_by_party_id=party_id,
        status="SENT",
        terms_text=terms_text,
        amount=amount,
        currency=currency,
        terms_hash=_terms_hash(terms_text, amount, currency, claim_ids),
        acknowledges_no_nonwaivable_waiver=True,
        expires_at=expires_at,
        supersedes_settlement_id=supersedes.id if supersedes is not None else None,
    )
    db.add(settlement)
    db.flush()

    for claim in claims:
        db.add(DisputeSettlementClaimLink(settlement_id=settlement.id, claim_id=claim.id))
        if claim.status not in ("NEGOTIATION",) and claim.status in (
            "OPEN", "RESPONSE_DUE", "EVIDENCE", "INTERNAL_REVIEW", "EXTERNAL_REFERRAL",
        ):
            transition_claim(claim, "NEGOTIATION", note="settlement proposed")

    _notify_settlement(
        db, case, proposed_by_role=proposed_by_role,
        title="A settlement has been proposed", message=f"A settlement proposal is awaiting your response on dispute case #{case.id}.",
    )

    db.commit()
    db.refresh(settlement)
    return settlement


def _expire_if_overdue(settlement: DisputeSettlement) -> bool:
    """ZR-ENG-CLR-010 Section 22 SENT -> EXPIRED (declared in Phase 4's own
    state machine, never written until now). Same lazy-check idiom as
    services/booking_expiry.py: mutates the already-loaded object and
    returns whether anything changed -- it does NOT call db.commit()
    itself. A write-path caller (respond_settlement/void_settlement, both
    of which already commit) persists the flip for free; a read-only
    caller shows the correct status in its response even without a write,
    self-healing on the next one -- same tolerance booking_expiry.py's own
    docstring states outright."""
    if settlement.status == "SENT" and settlement.expires_at is not None and datetime.now(timezone.utc) > settlement.expires_at:
        settlement.status = "EXPIRED"
        return True
    return False


def get_settlement_or_404(db: Session, settlement_id: int) -> DisputeSettlement:
    settlement = db.get(DisputeSettlement, settlement_id)
    if not settlement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Settlement not found")
    _expire_if_overdue(settlement)
    return settlement


def claim_ids_for_settlement(db: Session, settlement: DisputeSettlement) -> list[int]:
    return list(
        db.scalars(select(DisputeSettlementClaimLink.claim_id).where(DisputeSettlementClaimLink.settlement_id == settlement.id))
    )


def list_settlements_for_case(db: Session, case: DisputeResolutionCase) -> list[DisputeSettlement]:
    query = select(DisputeSettlement).where(DisputeSettlement.case_id == case.id).order_by(DisputeSettlement.created_at.desc())
    settlements = list(db.scalars(query))
    for settlement in settlements:
        _expire_if_overdue(settlement)
    return settlements


def assert_is_proposer(settlement: DisputeSettlement, *, guest: Guest | None = None, party_id: int | None = None) -> None:
    if guest is not None and settlement.proposed_by_guest_id == guest.id:
        return
    if party_id is not None and settlement.proposed_by_party_id == party_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the party who proposed this settlement can perform this action")


def assert_is_counterparty(settlement: DisputeSettlement, *, guest: Guest | None = None, party_id: int | None = None) -> None:
    if settlement.proposed_by_role == "RENTER":
        if party_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the host can respond to a renter's settlement proposal")
        return
    if settlement.proposed_by_role == "HOST":
        if guest is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the renter can respond to a host's settlement proposal")
        return


def void_settlement(db: Session, settlement: DisputeSettlement, *, guest: Guest | None = None, party_id: int | None = None) -> DisputeSettlement:
    assert_is_proposer(settlement, guest=guest, party_id=party_id)
    transition_settlement(settlement, "VOID")
    db.commit()
    db.refresh(settlement)
    return settlement


def respond_settlement(
    db: Session,
    settlement: DisputeSettlement,
    case: DisputeResolutionCase,
    *,
    guest: Guest | None = None,
    party_id: int | None = None,
    action: str,
    counter_terms_text: str | None = None,
    counter_amount: float | None = None,
    counter_currency: str | None = None,
    counter_expires_at: datetime | None = None,
) -> DisputeSettlement:
    if action not in ("ACCEPT", "REJECT", "COUNTER"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "action must be ACCEPT, REJECT or COUNTER")
    assert_is_counterparty(settlement, guest=guest, party_id=party_id)

    settlement.responded_by_guest_id = guest.id if guest is not None else None
    settlement.responded_by_party_id = party_id
    settlement.responded_at = datetime.now(timezone.utc)

    if action == "ACCEPT":
        transition_settlement(settlement, "ACCEPTED")
        transition_settlement(settlement, "EFFECTIVE")
        settlement.effective_at = datetime.now(timezone.utc)
        # Section 23 `accepted_party_snapshots` -- see the column's own
        # docstring for why this is frozen here rather than left to a live
        # join against responded_by_guest_id/responded_by_party_id.
        settlement.accepted_party_snapshot = {
            "responder_role": "HOST" if settlement.proposed_by_role == "RENTER" else "RENTER",
            "responder_guest_id": guest.id if guest is not None else None,
            "responder_party_id": party_id,
            "responded_at": settlement.responded_at.isoformat(),
            "terms_hash": settlement.terms_hash,
            "amount": float(settlement.amount) if settlement.amount is not None else None,
            "currency": settlement.currency,
        }
        for claim_id in claim_ids_for_settlement(db, settlement):
            claim = db.get(DisputeResolutionClaim, claim_id)
            if claim:
                transition_claim(claim, "SETTLED")
                claim.outcome = "SETTLED"
                claim.decided_at = datetime.now(timezone.utc)
                record_dispute_decision(
                    db, claim, outcome="SETTLED", basis="SETTLEMENT_ACCEPTED", authority="settlement", settlement_id=settlement.id,
                )
        _sync_case_status_after_claim_change(case)
        db.commit()
        db.refresh(settlement)
        return settlement

    if action == "REJECT":
        transition_settlement(settlement, "REJECTED")
        db.commit()
        db.refresh(settlement)
        return settlement

    # COUNTER: this row becomes terminal (Q24 -- its acceptance target is
    # invalidated by construction, not by mutating it), and the response
    # itself is a brand-new proposal from the responder's side.
    transition_settlement(settlement, "COUNTERED")
    db.flush()

    responder_role = "HOST" if guest is None else "RENTER"
    claim_ids = claim_ids_for_settlement(db, settlement)
    counter = propose_settlement(
        db, case,
        guest=guest if responder_role == "RENTER" else None,
        party_id=party_id if responder_role == "HOST" else None,
        claim_ids=claim_ids,
        terms_text=counter_terms_text if counter_terms_text is not None else settlement.terms_text,
        amount=counter_amount if counter_amount is not None else settlement.amount,
        currency=counter_currency if counter_currency is not None else settlement.currency,
        expires_at=counter_expires_at,
        acknowledges_no_nonwaivable_waiver=True,
        supersedes=settlement,
    )
    db.commit()
    db.refresh(settlement)
    return counter
