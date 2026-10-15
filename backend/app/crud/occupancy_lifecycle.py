"""Ending a tenancy without an admin.

A tenancy ends once both of these hold:

1. Its end date has arrived -- a termination case's effective date (set by
   the rules, by the parties, or by an outside order), or the agreement's
   own end date.
2. The room has been handed back -- the host confirmed move-out, or the
   renter said they'd left and the host didn't confirm within
   tenancy.move_out_confirm_days (silence counts as confirmation).

Until both hold the tenancy simply continues (a renter still in the room
past the end date stays a tenant). Called right after a move-out step and
hourly by the scheduler; idempotent because an ENDED occupancy is skipped."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.tenancy_settlement import settle_ended_tenancy
from app.models.occupancy import Occupancy
from app.models.occupancy_activation import OccupancyHandoverEvent
from app.models.termination_case import MUTUAL_CAUSE_CODES, TerminationCase

_GROUND_BASIS = {"RENTER_ORDINARY_EARLY_EXIT": "NOTICE"}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _confirm_days(occupancy: Occupancy) -> int:
    from app.services.policy import get_policy

    return int(get_policy(occupancy.listing.market_release if occupancy.listing else None, "tenancy.move_out_confirm_days"))


def handed_back_on(db: Session, occupancy: Occupancy) -> date | None:
    """The day the room was handed back, or None if it hasn't been yet."""
    events = {
        e.event_type: e for e in db.scalars(
            select(OccupancyHandoverEvent).where(OccupancyHandoverEvent.occupancy_id == occupancy.id)
            .order_by(OccupancyHandoverEvent.id)
        )
    }
    confirmed = events.get("HOST_MOVE_OUT_CONFIRMED")
    if confirmed is not None:
        return _aware(confirmed.created_at).date()
    ready = events.get("MOVE_OUT_READY")
    if ready is not None and datetime.now(timezone.utc) - _aware(ready.created_at) >= timedelta(days=_confirm_days(occupancy)):
        return _aware(ready.created_at).date()
    return None


def try_end_occupancy(db: Session, occupancy: Occupancy) -> bool:
    """Ends the tenancy if its date has arrived and the room is handed back.
    Returns True if it ended it."""
    from app.crud.occupancy import end_occupancy

    if occupancy.status != "ACTIVE":
        return False
    moved_out = handed_back_on(db, occupancy)
    if moved_out is None:
        return False
    today = date.today()
    case = db.scalar(
        select(TerminationCase).where(
            TerminationCase.occupancy_id == occupancy.id, TerminationCase.status == "EFFECTIVE_DATE_SET",
        ).order_by(TerminationCase.id.desc())
    )
    if case is not None:
        end_on = case.adjudicated_effective_date or case.effective_termination_date
        if end_on is None or end_on > today:
            return False
        basis = "MUTUAL_SURRENDER" if case.cause_code in MUTUAL_CAUSE_CODES else _GROUND_BASIS.get(case.cause_code, "STATUTORY_GROUND")
        end_occupancy(
            db, occupancy, None, basis=basis, termination_case_id=case.id, move_out_date=moved_out,
            liability_end_date=end_on, termination_effective_date=end_on,
        )
        settle_ended_tenancy(db, occupancy, case, end_on)
        return True
    if occupancy.expected_end_date is not None and occupancy.expected_end_date <= today:
        end_occupancy(
            db, occupancy, None, basis="NOTICE", move_out_date=moved_out, already_adjudicated=True,
            liability_end_date=occupancy.expected_end_date, termination_effective_date=occupancy.expected_end_date,
        )
        settle_ended_tenancy(db, occupancy, None, occupancy.expected_end_date)
        return True
    return False


def end_due_occupancies(db: Session) -> int:
    """Scheduler job: every active tenancy with move-out recorded."""
    ids = list(db.scalars(
        select(Occupancy.id).join(OccupancyHandoverEvent, OccupancyHandoverEvent.occupancy_id == Occupancy.id)
        .where(Occupancy.status == "ACTIVE", OccupancyHandoverEvent.event_type.in_(("HOST_MOVE_OUT_CONFIRMED", "MOVE_OUT_READY")))
        .distinct()
    ))
    ended = 0
    for occupancy_id in ids:
        occupancy = db.get(Occupancy, occupancy_id)
        if occupancy is not None and try_end_occupancy(db, occupancy):
            ended += 1
    return ended
