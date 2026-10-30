"""Termination decided between the parties instead of by an admin.

A case the platform can't date on its own (a contested ground, or any joint
tenancy) opens AWAITING_RESPONSE with a proposed end date. Everyone else on
the tenancy -- the host and every tenant, minus whoever proposed -- answers:

- ACCEPT: once all have accepted, the date is set (EFFECTIVE_DATE_SET).
- COUNTER: proposes another date; a new round starts and everyone else,
  including the original proposer, answers that.
- CONTEST: the case becomes CONTESTED and the tenancy carries on. It is
  settled by an outside order (court/tribunal) that either party records:
  the tenancy ends on the ordered date, or continues.

No answer by the deadline: for a ground, silence counts as acceptance; a
mutual surrender needs everyone's actual agreement, so it lapses
(SURRENDER_DECLINED) instead. The scheduler applies deadlines hourly."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.events import emit_event
from app.crud.party import party_id_for_listing
from app.models.guest import Guest
from app.models.occupancy import OccupancyCoTenant
from app.models.termination_case import (
    EXTERNAL_DECISION_OUTCOMES,
    MUTUAL_CAUSE_CODES,
    TERMINATION_RESPONSES,
    TerminationCase,
    TerminationCaseResponse,
)
from app.models.user_account import UserAccount

HOST = "HOST"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def tenant_ids(db: Session, case: TerminationCase) -> list[str]:
    ids = [case.occupancy.guest_id]
    ids += [g for g in db.scalars(select(OccupancyCoTenant.guest_id).where(OccupancyCoTenant.occupancy_id == case.occupancy_id)) if g not in ids]
    return ids


def required_responders(db: Session, case: TerminationCase) -> list[str]:
    """Everyone on the tenancy except the current proposer."""
    return [p for p in [HOST, *tenant_ids(db, case)] if p != case.proposer]


def pending_responders(db: Session, case: TerminationCase) -> list[str]:
    if case.status != "AWAITING_RESPONSE":
        return []
    answered = {r.responder for r in case.responses if r.response_round == case.response_round}
    return [p for p in required_responders(db, case) if p not in answered]


def _response_days(case: TerminationCase) -> int:
    from app.services.policy import get_policy

    listing = case.occupancy.listing
    return int(get_policy(listing.market_release if listing else None, "termination.response_days"))


def start_response_round(db: Session, case: TerminationCase, *, proposer: str) -> None:
    """Sets up the first round on a just-opened case (caller commits)."""
    case.status = "AWAITING_RESPONSE"
    case.proposer = proposer
    case.response_round = 1
    case.deemed_on_silence = case.cause_code not in MUTUAL_CAUSE_CODES
    case.response_due_at = _now() + timedelta(days=_response_days(case))
    case.effective_termination_date = None
    db.flush()
    _notify_responders(db, case, title="Respond to a proposal to end the tenancy")


# --- Who's acting ------------------------------------------------------------


def actor_for(db: Session, case: TerminationCase, *, guest: Guest | None = None, host_user: UserAccount | None = None) -> str:
    """The responder key for the caller, or 403 if they aren't on this tenancy."""
    if host_user is not None:
        if not host_user.party_id or party_id_for_listing(case.occupancy.listing) != host_user.party_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This tenancy isn't on your property")
        return HOST
    if guest is not None and guest.id in tenant_ids(db, case):
        return guest.id
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You aren't a party to this tenancy")


# --- Notifications -----------------------------------------------------------


def _notify(db: Session, case: TerminationCase, who: str, *, title: str, message: str, event: str) -> None:
    kwargs = dict(
        title=title, message=message, notification_type=f"termination_case.{event}",
        related_entity_type="termination_case", related_entity_id=f"{case.id}:{case.response_round}:{event}:{who}",
    )
    if who == HOST:
        notif_crud.notify_user_by_party(db, party_id_for_listing(case.occupancy.listing), **kwargs)
    else:
        guest = db.get(Guest, who)
        if guest is not None:
            notif_crud.notify_user_by_guest(db, guest, **kwargs)


def _notify_responders(db: Session, case: TerminationCase, *, title: str) -> None:
    name = case.occupancy.listing.name if case.occupancy.listing else "your rental"
    due = _aware(case.response_due_at).date().isoformat()
    silence = "If you don't respond by then, the date is accepted." if case.deemed_on_silence else (
        "If not everyone agrees by then, the proposal lapses."
    )
    for who in required_responders(db, case):
        _notify(
            db, case, who, title=title, event="response_requested",
            message=f'End date {case.earliest_effective_date.isoformat()} proposed for "{name}". Respond by {due}. {silence}',
        )


def _notify_all(db: Session, case: TerminationCase, *, title: str, message: str, event: str) -> None:
    for who in [HOST, *tenant_ids(db, case)]:
        _notify(db, case, who, title=title, message=message, event=event)


# --- Outcomes ----------------------------------------------------------------


def _set_date(db: Session, case: TerminationCase, *, basis: str, authority: str, approved_by_user_id: int | None = None) -> None:
    from app.crud.termination import _record_decision

    previous = case.status
    case.status = "EFFECTIVE_DATE_SET"
    case.effective_termination_date = case.earliest_effective_date
    case.response_due_at = None
    _record_decision(
        db, case, basis=basis, authority=authority, effective_termination_at=case.effective_termination_date,
        approved_by_user_id=approved_by_user_id,
    )
    emit_event(
        db, "termination.effective_date_set", "termination_case", str(case.id),
        {"occupancyId": case.occupancy_id, "effectiveDate": case.effective_termination_date.isoformat(), "basis": basis},
        actor_kind="system" if authority == "system" else "parties", actor_id="", previous_state=previous, new_state=case.status,
    )
    _notify_all(
        db, case, event="date_set", title="Tenancy end date set",
        message=f"The tenancy ends on {case.effective_termination_date.isoformat()}.",
    )


def respond(
    db: Session, case: TerminationCase, *, response: str, guest: Guest | None = None, host_user: UserAccount | None = None,
    proposed_date: date | None = None, note: str = "",
) -> TerminationCase:
    who = actor_for(db, case, guest=guest, host_user=host_user)
    if case.status != "AWAITING_RESPONSE":
        raise HTTPException(status.HTTP_409_CONFLICT, "This proposal isn't waiting for a response")
    if response not in TERMINATION_RESPONSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"response must be one of {list(TERMINATION_RESPONSES)}")
    if who not in pending_responders(db, case):
        raise HTTPException(status.HTTP_409_CONFLICT, "You've already responded, or this is your own proposal")

    if response == "COUNTER":
        if proposed_date is None or proposed_date < date.today():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Propose a future end date")
        if proposed_date == case.earliest_effective_date:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That's the date already proposed -- accept it instead")

    db.add(TerminationCaseResponse(
        termination_case_id=case.id, response_round=case.response_round, responder=who,
        responder_user_id=host_user.id if host_user is not None else (guest.user_account_id if guest else None),
        response=response, proposed_date=proposed_date if response == "COUNTER" else None, note=note.strip()[:2000],
    ))
    db.flush()

    if response == "CONTEST":
        case.status = "CONTESTED"
        case.response_due_at = None
        _notify_all(
            db, case, event="contested", title="End of tenancy contested",
            message="The proposed end date was contested. The tenancy continues unless a court or tribunal orders otherwise.",
        )
    elif response == "COUNTER":
        case.earliest_effective_date = proposed_date
        case.proposer = who
        case.response_round += 1
        case.response_due_at = _now() + timedelta(days=_response_days(case))
        db.flush()
        _notify_responders(db, case, title="A different end date was proposed")
    else:
        db.refresh(case)
        if not pending_responders(db, case):
            _set_date(db, case, basis="PARTIES_AGREED", authority="parties", approved_by_user_id=host_user.id if host_user else None)
    db.commit()
    db.refresh(case)
    return case


def record_external_decision(
    db: Session, case: TerminationCase, *, outcome: str, order_ref: str, effective_date: date | None = None,
    liability_amount: float | None = None, guest: Guest | None = None, host_user: UserAccount | None = None,
) -> TerminationCase:
    """A court or tribunal decided a contested case; either party records it
    with a reference to the order. The platform applies it -- no admin."""
    actor_for(db, case, guest=guest, host_user=host_user)
    if case.status != "CONTESTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a contested case is settled by an outside decision")
    if outcome not in EXTERNAL_DECISION_OUTCOMES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"outcome must be one of {list(EXTERNAL_DECISION_OUTCOMES)}")
    if not order_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the order's reference")
    from app.crud.termination import _record_decision

    if liability_amount is not None and liability_amount < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A liability amount can't be negative")
    case.external_outcome = outcome
    case.external_order_ref = order_ref.strip()[:500]
    if liability_amount is not None:
        case.tribunal_liability_amount = liability_amount
        case.tribunal_liability_reason = f"External order {case.external_order_ref}"
    if outcome == "ENDS_ON_DATE":
        if effective_date is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Give the end date the order sets")
        case.adjudicated_effective_date = effective_date
        case.adjudicated_effective_date_reason = f"External order {case.external_order_ref}"
        case.earliest_effective_date = effective_date
        case.status = "EFFECTIVE_DATE_SET"
        case.effective_termination_date = effective_date
        _record_decision(
            db, case, basis="EXTERNAL_ORDER", authority="external", effective_termination_at=effective_date,
            external_order_ref=case.external_order_ref[:100],
        )
        message = f"An outside order ends the tenancy on {effective_date.isoformat()} (ref {case.external_order_ref})."
    else:
        case.status = "REJECTED_PATHWAY"
        _record_decision(
            db, case, basis="EXTERNAL_ORDER", authority="external", effective_termination_at=None,
            external_order_ref=case.external_order_ref[:100], reason="Tenancy continues",
        )
        message = f"An outside order says the tenancy continues (ref {case.external_order_ref})."
    _notify_all(db, case, event="external_decision", title="Outside decision recorded", message=message)
    db.commit()
    db.refresh(case)
    return case


def withdraw_own(db: Session, case: TerminationCase, *, guest: Guest | None = None, host_user: UserAccount | None = None) -> TerminationCase:
    """The side that opened the case withdraws it while it's still undecided."""
    who = actor_for(db, case, guest=guest, host_user=host_user)
    opened_by = HOST if case.initiator_guest_id is None else case.initiator_guest_id
    if who != opened_by:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the side that opened this case can withdraw it")
    if case.status not in ("AWAITING_RESPONSE", "CONTESTED", "SURRENDER_PROPOSED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This case can no longer be withdrawn")
    case.status = "WITHDRAWN"
    case.withdrawn_at = _now()
    case.response_due_at = None
    _notify_all(db, case, event="withdrawn", title="End of tenancy withdrawn", message="The proposal to end the tenancy was withdrawn.")
    db.commit()
    db.refresh(case)
    return case


def apply_due_responses(db: Session) -> int:
    """Scheduler job: proposals whose deadline has passed. A ground is
    accepted on silence; a mutual surrender without everyone's agreement
    lapses. Idempotent -- a resolved case is no longer AWAITING_RESPONSE."""
    now = _now()
    resolved = 0
    for case in db.scalars(select(TerminationCase).where(
        TerminationCase.status == "AWAITING_RESPONSE", TerminationCase.response_due_at.is_not(None),
    )):
        if _aware(case.response_due_at) > now:
            continue
        if case.deemed_on_silence:
            _set_date(db, case, basis="DEEMED_ACCEPTED", authority="system")
        else:
            case.status = "SURRENDER_DECLINED"
            case.response_due_at = None
            _notify_all(
                db, case, event="lapsed", title="Proposal to end the tenancy lapsed",
                message="Not everyone agreed in time, so the tenancy continues as before.",
            )
        resolved += 1
        db.commit()
    return resolved


def route_pending_reviews_to_parties(db: Session) -> int:
    """Scheduler job: a case left in the retired PENDING_REVIEW state (opened
    before the parties decided these themselves) was waiting on an admin who no
    longer has that step. It now goes to the other side as an ordinary proposal
    -- accept, counter or contest -- so host and renter settle it between them.
    The proposed date is the one already on the case if it is still ahead,
    otherwise the minimum notice from today. Idempotent: routed cases are
    AWAITING_RESPONSE."""
    from app.crud.termination import _min_notice_days

    routed = 0
    for case in db.scalars(select(TerminationCase).where(TerminationCase.status == "PENDING_REVIEW")):
        today = _now().date()
        if case.earliest_effective_date is None or case.earliest_effective_date < today:
            case.earliest_effective_date = today + timedelta(days=_min_notice_days(case.occupancy))
        proposer = case.initiator_guest_id if case.initiator_guest_id is not None else HOST
        start_response_round(db, case, proposer=proposer)
        routed += 1
        db.commit()
    return routed
