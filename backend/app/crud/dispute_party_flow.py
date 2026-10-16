"""Host <-> renter disputes settled without an admin.

A claim between a renter and a host (authority A1 or A2 -- booking, property
condition, sublet, deposit...) moves forward on the parties' own actions and
deadlines:

1. The respondent (the other side from the claimant) answers: ACCEPT upholds
   the claim; CONTEST opens a negotiation window (settlement offers already
   work through crud/dispute_settlement.py).
2. No answer by the PARTY_RESPONSE deadline: the claim is accepted.
3. Negotiation window over with no settlement: the claim is referred to the
   outside forum for its authority class (tribunal, deposit scheme...).
4. Either party records the outside forum's decision; it's applied and the
   case closes once every claim is resolved.

Claims about Zoiko Rooms itself (A0) and safety claims (A6) stay with Zoiko
staff -- neither party can decide those."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import dispute_deadline as deadline_crud
from app.crud.disputes import (
    _notify_case_participants,
    _sync_case_status_after_claim_change,
    close_case_when_resolved,
    record_dispute_decision,
)
from app.models.dispute import DisputeResolutionCase, DisputeResolutionClaim
from app.models.dispute_deadline import DisputeDeadline
from app.services.dispute_state_machine import transition_claim

PARTY_RESOLVED_AUTHORITIES = ("A1", "A2")
_ANSWERABLE = ("OPEN", "RESPONSE_DUE", "EVIDENCE")
RESPONSES = ("ACCEPT", "CONTEST")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _negotiation_days(case: DisputeResolutionCase) -> int:
    from app.services.policy import get_policy

    listing = case.occupancy.listing if case.occupancy is not None else None
    return int(get_policy(listing.market_release if listing else None, "dispute.negotiation_days"))


def respondent_role(claim: DisputeResolutionClaim) -> str:
    return "HOST" if claim.claimant_role == "RENTER" else "RENTER"


def _uphold(db: Session, claim: DisputeResolutionClaim, *, basis: str) -> None:
    transition_claim(claim, "UPHELD", note=basis.lower())
    claim.outcome = "UPHELD"
    claim.decided_at = _now()
    record_dispute_decision(db, claim, outcome="UPHELD", basis=basis, authority="parties")
    _meet_deadlines(db, claim, ("PARTY_RESPONSE", "EVIDENCE_CLOSE"))


def _meet_deadlines(db: Session, claim: DisputeResolutionClaim, types: tuple[str, ...]) -> None:
    for d in db.scalars(select(DisputeDeadline).where(
        DisputeDeadline.claim_id == claim.id, DisputeDeadline.deadline_type.in_(types), DisputeDeadline.status == "PENDING",
    )):
        d.status = "MET"


def _finish(db: Session, case: DisputeResolutionCase) -> None:
    _sync_case_status_after_claim_change(case)
    db.commit()
    db.refresh(case)
    close_case_when_resolved(db, case)


def respond_to_claim(
    db: Session, case: DisputeResolutionCase, claim: DisputeResolutionClaim, *, role: str, response: str, note: str = "",
) -> DisputeResolutionClaim:
    if claim.case_id != case.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This claim does not belong to this case")
    if claim.authority_class not in PARTY_RESOLVED_AUTHORITIES:
        raise HTTPException(status.HTTP_409_CONFLICT, "This claim is handled by Zoiko Rooms, not between you")
    if role != respondent_role(claim):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the other side can answer this claim")
    if response not in RESPONSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"response must be one of {list(RESPONSES)}")
    if claim.status not in _ANSWERABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This claim isn't waiting for an answer (status {claim.status})")

    if response == "ACCEPT":
        _uphold(db, claim, basis="RESPONDENT_ACCEPTED")
        message = f"Claim #{claim.id} was accepted by the other side."
    else:
        transition_claim(claim, "NEGOTIATION", note="respondent contested")
        if note:
            claim.reason_code = note.strip()[:100]
        _meet_deadlines(db, claim, ("PARTY_RESPONSE",))
        due = _now() + timedelta(days=_negotiation_days(case))
        db.add(DisputeDeadline(
            case_id=case.id, claim_id=claim.id, deadline_type="EVIDENCE_CLOSE", due_at=due, status="PENDING",
            source="SYSTEM_DEFAULT", created_at=_now(), reminder_at=deadline_crud.compute_reminder_at(_now(), due),
        ))
        message = (
            f"Claim #{claim.id} was contested. Try to agree a settlement by {due.date().isoformat()}; "
            "if you can't, it goes to the outside body that handles this kind of dispute."
        )
    db.flush()
    _notify_case_participants(db, case, title="A dispute claim was answered", message=message, notification_type="dispute_claim.answered")
    _finish(db, case)
    db.refresh(claim)
    return claim


def apply_dispute_deadlines(db: Session) -> int:
    """Scheduler job. An unanswered host<->renter claim past its response
    deadline is accepted; a contested one past its negotiation window with no
    settlement is referred to the outside forum. Idempotent: each deadline
    is marked MET once acted on."""
    now = _now()
    changed = 0
    due = db.scalars(select(DisputeDeadline).where(
        DisputeDeadline.status == "PENDING", DisputeDeadline.deadline_type.in_(("PARTY_RESPONSE", "EVIDENCE_CLOSE")),
        DisputeDeadline.claim_id.is_not(None),
    ).order_by(DisputeDeadline.id)).all()
    for deadline in due:
        if _aware(deadline.due_at) > now:
            continue
        claim = db.get(DisputeResolutionClaim, deadline.claim_id)
        if claim is None or claim.authority_class not in PARTY_RESOLVED_AUTHORITIES:
            continue
        case = claim.case
        if deadline.deadline_type == "PARTY_RESPONSE" and claim.status in _ANSWERABLE:
            _uphold(db, claim, basis="DEEMED_ACCEPTED_NO_RESPONSE")
            _notify_case_participants(
                db, case, title="Dispute claim accepted",
                message=f"Claim #{claim.id} wasn't answered in time, so it's accepted.",
                notification_type="dispute_claim.deemed_accepted",
            )
        elif deadline.deadline_type == "EVIDENCE_CLOSE" and claim.status == "NEGOTIATION":
            transition_claim(claim, "EXTERNAL_REFERRAL", note="no settlement within the negotiation window")
            deadline.status = "MET"
            _notify_case_participants(
                db, case, title="Dispute goes to an outside body",
                message=(
                    f"No settlement was reached on claim #{claim.id}. Take it to the body that handles this kind of "
                    "dispute (for a deposit, your deposit protection scheme; otherwise a tribunal or court), then record "
                    "its decision on the dispute page."
                ),
                notification_type="dispute_claim.referred",
            )
        else:
            deadline.status = "MET"
            db.commit()
            continue
        changed += 1
        _finish(db, case)
    return changed


# --- Outside forum, recorded by the parties ---------------------------------


def _party_claims(case: DisputeResolutionCase, claim_ids: list[int]) -> list[DisputeResolutionClaim]:
    claims = [c for c in case.claims if c.id in set(claim_ids)]
    if not claims or len(claims) != len(set(claim_ids)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found on this case")
    if any(c.authority_class not in PARTY_RESOLVED_AUTHORITIES for c in claims):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only host<->renter claims are taken to an outside body by the parties")
    return claims


def file_external_by_party(
    db: Session, case: DisputeResolutionCase, *, claim_ids: list[int], authority_type: str,
    authority_name: str, external_reference: str,
):
    """Either party records that they've taken claims to the outside body
    (tribunal, deposit scheme...)."""
    from app.crud.dispute_external_proceeding import file_proceeding

    _party_claims(case, claim_ids)
    return file_proceeding(
        db, case, None, authority_type=authority_type, authority_name=authority_name.strip()[:200],
        external_reference=external_reference.strip()[:200], claim_ids=claim_ids,
    )


def record_external_by_party(
    db: Session, case: DisputeResolutionCase, proceeding, *, outcome: str, decision_date, finality_state: str,
    reason_code: str = "", outcome_evidence_id: int | None = None,
):
    """Either party records the outside body's decision; it's applied to the
    claims and the case closes once everything on it is resolved."""
    from app.crud.dispute_external_proceeding import claim_ids_for_proceeding, record_decision

    if proceeding.case_id != case.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proceeding not found on this case")
    _party_claims(case, claim_ids_for_proceeding(db, proceeding))
    updated = record_decision(
        db, proceeding, None, outcome=outcome, decision_date=decision_date, finality_state=finality_state,
        outcome_evidence_id=outcome_evidence_id, reason_code=reason_code,
    )
    db.refresh(case)
    _notify_case_participants(
        db, case, title="Outside decision recorded",
        message=f"The outside body's decision ({outcome.replace('_', ' ').lower()}) was recorded on the dispute.",
        notification_type="dispute_external.decision_recorded",
    )
    db.commit()
    close_case_when_resolved(db, case)
    return updated
