"""A shared-room co-tenant's rent share, recorded as ordinary payment obligations.

An ADD_CO_TENANT lives under the renter's own tenancy and pays the renter directly -- there is no
agreement or occupancy of their own, and Zoiko Rooms never holds the money. What the app does keep is the
record: each month's share becomes a rent obligation from the co-tenant to the renter, which the co-tenant
marks paid and the renter confirms received, exactly like any other payment (bank transfer, UPI or cash).
The amount is the rent share the renter proposed when they asked the host (the same capped figure the host
approved); with none proposed there is nothing to record.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rental_payment import RentalPaymentObligation
from app.models.sublet_request import SubletRequest

ARRANGEMENT = "ADD_CO_TENANT"
# Rent for next month shows up this many days before it is due.
LEAD_DAYS = 30
OPEN_STATUSES = ("UPCOMING", "DUE", "OVERDUE")


def agreed_rent(sublet: SubletRequest) -> float | None:
    rent = (sublet.policy_snapshot or {}).get("proposed_monthly_rent")
    try:
        value = float(rent) if rent is not None else None
    except (TypeError, ValueError):
        return None
    return value if value and value > 0 else None


def is_billable(sublet: SubletRequest) -> bool:
    return sublet.arrangement_type == ARRANGEMENT and sublet.status == "approved" and sublet.ended_at is None and agreed_rent(sublet) is not None


def _add_months(d: date, months: int) -> date:
    from app.crud.sublet import _add_months as add_months

    return add_months(d, months)


def _first_due(sublet: SubletRequest) -> date:
    decided = (sublet.decided_at or datetime.now(timezone.utc)).date()
    start = sublet.proposed_start_date
    return start if start is not None and start > decided else decided


def _last_due(sublet: SubletRequest) -> date | None:
    ends = [d for d in (sublet.proposed_end_date, sublet.current_occupancy.expected_end_date if sublet.current_occupancy else None) if d]
    return min(ends) if ends else None


def sync_shared_room_rent(db: Session, sublet: SubletRequest, *, today: date | None = None) -> int:
    """Makes sure every month's rent share up to LEAD_DAYS ahead exists. Idempotent. Returns how many were added."""
    if not is_billable(sublet):
        return 0
    from app.crud.rental_payment import create_obligation, sublet_requester_party_id
    from app.crud.sublet import _guest_for_proposed_party

    recipient_party_id = sublet_requester_party_id(db, sublet)
    if recipient_party_id is None or sublet.proposed_renter_party_id is None:
        return 0
    tenant = _guest_for_proposed_party(db, sublet.proposed_renter_party_id)
    listing = sublet.current_occupancy.listing if sublet.current_occupancy else None
    if listing is None:
        return 0

    today = today or date.today()
    horizon = today + timedelta(days=LEAD_DAYS)
    last_due = _last_due(sublet)
    rent = float(agreed_rent(sublet))
    first = _first_due(sublet)
    have = {
        row.due_date for row in db.scalars(select(RentalPaymentObligation).where(
            RentalPaymentObligation.sublet_request_id == sublet.id, RentalPaymentObligation.obligation_type == "RENT"))
    }

    added = 0
    for month in range(0, 120):
        due = _add_months(first, month)
        if due > horizon or (last_due is not None and due > last_due):
            break
        if due in have:
            continue
        obligation = create_obligation(
            db, obligation_type="RENT", tenant_guest_id=tenant.id, recipient_party_id=recipient_party_id,
            amount=rent, currency=listing.currency, due_date=due,
        )
        obligation.sublet_request_id = sublet.id
        obligation.payee_type = "SUBLESSOR"
        obligation.payee_basis = "SUBLET_APPROVAL_PERMITS_COLLECTION"
        obligation.payee_authority_ref = f"sublet_request:{sublet.id}"
        obligation.period_start = due
        obligation.period_end = _add_months(due, 1) - timedelta(days=1)
        obligation.payment_reference = f"ZR-CT-{sublet.id}-{due:%Y%m}"
        db.commit()
        added += 1
    return added


def cancel_unpaid_rent_after(db: Session, sublet: SubletRequest, *, on_or_after: date) -> int:
    """The co-tenancy ended: rent that hasn't started being paid and falls after the end is no longer owed."""
    cancelled = 0
    for obligation in db.scalars(select(RentalPaymentObligation).where(RentalPaymentObligation.sublet_request_id == sublet.id)):
        if obligation.status in OPEN_STATUSES and not obligation.records and obligation.due_date >= on_or_after:
            obligation.status = "CANCELLED"
            cancelled += 1
    if cancelled:
        db.commit()
    return cancelled


def sweep_shared_room_rent(db: Session) -> int:
    """Scheduler job: every active shared room gets its upcoming rent share recorded."""
    created = 0
    for sublet in db.scalars(select(SubletRequest).where(
            SubletRequest.arrangement_type == ARRANGEMENT, SubletRequest.status == "approved", SubletRequest.ended_at.is_(None))):
        created += sync_shared_room_rent(db, sublet)
    return created
