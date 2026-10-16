"""What happens to money when a tenancy ends -- automatically, no admin.

1. Rent that falls after the end date and hasn't been paid is cancelled.
2. A termination case's refund entitlement (money the platform itself
   collected, e.g. at signing) is calculated, approved and executed by the
   rules (crud/refund_entitlement.py) -- the calculation follows the market
   pack, so there is nothing for a person to approve.
3. Rent the renter paid straight to the host for days after the end date
   becomes a HostRefund the host owes back: the host marks it paid, the
   renter confirms receipt (or is deemed to after the confirm window), and an
   unpaid one past its deadline turns OVERDUE.

Runs right after a tenancy ends (crud/occupancy_lifecycle.py) and the
deadline parts hourly from the scheduler."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.party import party_id_for_listing
from app.models.host_refund import HostRefund
from app.models.occupancy import Occupancy
from app.models.refund_entitlement import RefundEntitlement
from app.models.rental_payment import RentalPaymentObligation
from app.models.termination_case import TerminationCase

logger = logging.getLogger("uvicorn.error")

_UNPAID = ("UPCOMING", "DUE", "OVERDUE")
_RECEIVED = ("CONFIRMED",)


def _policy(occupancy: Occupancy, key: str) -> int:
    from app.services.policy import get_policy

    return int(get_policy(occupancy.listing.market_release if occupancy.listing else None, key))


def _unearned_share(ob: RentalPaymentObligation, end: date) -> float:
    """The part of a paid rent obligation covering days after `end`."""
    amount = float(ob.amount)
    if ob.period_start and ob.period_end:
        if ob.period_start > end:
            return amount
        if ob.period_end <= end:
            return 0.0
        total_days = (ob.period_end - ob.period_start).days + 1
        unearned_days = (ob.period_end - end).days
        return round(amount * unearned_days / total_days, 2)
    return amount if ob.due_date > end else 0.0


def settle_ended_tenancy(db: Session, occupancy: Occupancy, case: TerminationCase | None, end: date) -> None:
    rent = list(db.scalars(select(RentalPaymentObligation).where(
        RentalPaymentObligation.occupancy_id == occupancy.id, RentalPaymentObligation.obligation_type == "RENT",
    )))

    # 1. Unpaid rent after the end date isn't owed.
    for ob in rent:
        if ob.status in _UNPAID and ob.due_date > end:
            ob.status = "CANCELLED"
            ob.waived_reason = f"Tenancy ended {end.isoformat()}"

    # 2. Platform-collected money: the termination case's refund entitlement.
    legacy_gross = 0.0
    if case is not None:
        legacy_gross = _settle_refund_entitlement(db, case)

    # 3. Rent paid straight to the host for days after the end date.
    unearned = round(sum(_unearned_share(ob, end) for ob in rent if ob.status in _RECEIVED), 2)
    liability = float(case.tribunal_liability_amount or 0) if case is not None else 0.0
    # A liability the platform refund already absorbed isn't taken twice.
    remaining_liability = max(0.0, liability - legacy_gross)
    owed = round(max(0.0, unearned - remaining_liability), 2)
    exists = db.scalar(select(HostRefund.id).where(HostRefund.occupancy_id == occupancy.id))
    if owed >= 0.01 and not exists:
        currency = next((ob.currency for ob in rent), occupancy.listing.currency if occupancy.listing else "GBP")
        refund = HostRefund(
            occupancy_id=occupancy.id, termination_case_id=case.id if case else None, amount=owed, currency=currency,
            reason=f"Rent paid for days after the tenancy ended on {end.isoformat()}",
            due_by=date.today() + timedelta(days=_policy(occupancy, "refund.host_payment_days")),
        )
        db.add(refund)
        db.flush()
        listing_name = occupancy.listing.name if occupancy.listing else "the rental"
        notif_crud.notify_user_by_party(
            db, party_id_for_listing(occupancy.listing),
            title="Refund owed to your former renter",
            message=f'{currency} {owed:.2f} for "{listing_name}" -- pay it by {refund.due_by.isoformat()} and mark it paid.',
            notification_type="host_refund.due", related_entity_type="host_refund", related_entity_id=str(refund.id),
        )
        notif_crud.notify_user_by_guest(
            db, occupancy.guest,
            title="Your host owes you a refund",
            message=f'{currency} {owed:.2f} for "{listing_name}", due by {refund.due_by.isoformat()}.',
            notification_type="host_refund.due_for_renter", related_entity_type="host_refund", related_entity_id=str(refund.id),
        )
    db.commit()


def _settle_refund_entitlement(db: Session, case: TerminationCase) -> float:
    """Calculate -> approve -> execute as the system. Returns the gross
    refundable amount (0 if there was nothing the platform collected)."""
    from app.crud import refund_entitlement as re_crud

    done = db.scalar(select(RefundEntitlement).where(
        RefundEntitlement.termination_case_id == case.id, RefundEntitlement.status == "EXECUTED",
    ))
    if done is not None:
        return float(done.gross_refundable)
    if case.effective_termination_date is None:
        return 0.0
    try:
        entitlement = re_crud.calculate_refund_entitlement(db, case, None)
        if float(entitlement.gross_refundable) <= 0:
            return 0.0
        entitlement = re_crud.approve_refund_entitlement(db, entitlement, None)
        entitlement = re_crud.execute_refund_entitlement(db, entitlement, None)
        return float(entitlement.gross_refundable)
    except HTTPException:
        # e.g. nothing refundable to execute against -- the host refund below
        # still covers rent paid directly.
        db.rollback()
        logger.info("refund entitlement for termination case %s not settled automatically", case.id)
        return 0.0


# --- Host refund steps ---------------------------------------------------------


def _get_refund(db: Session, refund_id: int) -> HostRefund:
    refund = db.get(HostRefund, refund_id)
    if refund is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Refund not found")
    return refund


def host_refund_for_host(db: Session, refund_id: int, user) -> HostRefund:
    refund = _get_refund(db, refund_id)
    if not user.party_id or party_id_for_listing(refund.occupancy.listing) != user.party_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Refund not found")
    return refund


def host_refund_for_renter(db: Session, refund_id: int, user) -> HostRefund:
    from app.crud.guest import get_guest_for_user

    refund = _get_refund(db, refund_id)
    guest = get_guest_for_user(db, user)
    if guest is None or refund.occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Refund not found")
    return refund


def list_for_host(db: Session, user) -> list[HostRefund]:
    from app.models.property import Property
    from app.models.room import Room

    if not user.party_id:
        return []
    return list(db.scalars(
        select(HostRefund).join(Occupancy, Occupancy.id == HostRefund.occupancy_id)
        .join(Room, Room.id == Occupancy.room_id).join(Property, Property.id == Room.property_id)
        .where(Property.owner_party_id == user.party_id).order_by(HostRefund.id.desc())
    ))


def list_for_renter(db: Session, user) -> list[HostRefund]:
    from app.crud.guest import get_guest_for_user

    guest = get_guest_for_user(db, user)
    if guest is None:
        return []
    return list(db.scalars(
        select(HostRefund).join(Occupancy, Occupancy.id == HostRefund.occupancy_id)
        .where(Occupancy.guest_id == guest.id).order_by(HostRefund.id.desc())
    ))


def mark_paid(db: Session, refund: HostRefund, reference: str) -> HostRefund:
    if refund.status not in ("DUE", "OVERDUE", "NOT_RECEIVED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This refund isn't waiting for payment")
    refund.status = "MARKED_PAID"
    refund.payment_reference = reference.strip()[:200]
    refund.marked_paid_at = datetime.now(timezone.utc)
    notif_crud.notify_user_by_guest(
        db, refund.occupancy.guest, title="Your host says they've paid your refund",
        message=f"{refund.currency} {float(refund.amount):.2f}. Confirm once it arrives, or tell us it didn't.",
        notification_type="host_refund.marked_paid", related_entity_type="host_refund",
        related_entity_id=f"{refund.id}:{refund.marked_paid_at.timestamp():.0f}",
    )
    db.commit()
    db.refresh(refund)
    return refund


def confirm_received(db: Session, refund: HostRefund) -> HostRefund:
    if refund.status != "MARKED_PAID":
        raise HTTPException(status.HTTP_409_CONFLICT, "Your host hasn't marked this refund as paid yet")
    refund.status = "CONFIRMED"
    refund.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(refund)
    return refund


def report_not_received(db: Session, refund: HostRefund, note: str) -> HostRefund:
    if refund.status != "MARKED_PAID":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a refund marked as paid can be reported as not received")
    refund.status = "NOT_RECEIVED"
    refund.not_received_note = note.strip()[:1000]
    notif_crud.notify_user_by_party(
        db, party_id_for_listing(refund.occupancy.listing), title="Refund not received",
        message=f"Your former renter says the {refund.currency} {float(refund.amount):.2f} refund hasn't arrived.",
        notification_type="host_refund.not_received", related_entity_type="host_refund",
        related_entity_id=f"{refund.id}:{datetime.now(timezone.utc).timestamp():.0f}",
    )
    db.commit()
    db.refresh(refund)
    return refund


def apply_host_refund_deadlines(db: Session) -> int:
    """Scheduler job: MARKED_PAID past the confirm window -> CONFIRMED
    (deemed); DUE past due_by -> OVERDUE (both sides told)."""
    now = datetime.now(timezone.utc)
    changed = 0
    for refund in db.scalars(select(HostRefund).where(HostRefund.status.in_(("MARKED_PAID", "DUE")))):
        if refund.status == "MARKED_PAID":
            marked = refund.marked_paid_at if refund.marked_paid_at.tzinfo else refund.marked_paid_at.replace(tzinfo=timezone.utc)
            if now - marked >= timedelta(days=_policy(refund.occupancy, "refund.renter_confirm_days")):
                refund.status = "CONFIRMED"
                refund.confirmed_at = now
                refund.deemed_confirmed = True
                changed += 1
        elif refund.due_by < now.date():
            refund.status = "OVERDUE"
            for notify, kwargs in (
                (notif_crud.notify_user_by_party, dict(party_id=party_id_for_listing(refund.occupancy.listing))),
                (notif_crud.notify_user_by_guest, dict(guest=refund.occupancy.guest)),
            ):
                notify(
                    db, *kwargs.values(), title="Refund overdue",
                    message=f"The {refund.currency} {float(refund.amount):.2f} refund was due by {refund.due_by.isoformat()}.",
                    notification_type="host_refund.overdue", related_entity_type="host_refund", related_entity_id=str(refund.id),
                )
            changed += 1
    db.commit()
    return changed
