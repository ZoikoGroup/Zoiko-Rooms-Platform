"""Deposit returns and cancellation returns paid host -> renter directly
(models/rental_payment_return.py). The host records what they sent back;
the renter confirms it arrived or reports a problem. Zoiko only records --
it never holds, moves or sends back rental money."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.models.guest import Guest
from app.models.leasing import Agreement
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.rental_payment import RentalPaymentObligation
from app.models.rental_payment_return import RENTAL_PAYMENT_RETURN_KINDS, RentalPaymentReturn

# Money is sent back by the same direct methods rent is paid with.
RETURN_METHODS = ("BANK_TRANSFER", "UPI", "CASH", "OTHER")
# Which occupancy state each kind belongs to.
_KIND_REQUIRES_OCCUPANCY_STATUS = {"DEPOSIT_RETURN": "ENDED", "CANCELLATION_RETURN": "CANCELLED"}
# A return that still counts against what can be sent back -- a DISPUTED one
# doesn't (the renter says it never arrived), so the host can record it again.
_COUNTING_STATUSES = ("RECORDED", "CONFIRMED")


def _round2(value) -> float:
    return round(float(value or 0), 2)


def _booking_obligations(db: Session, occupancy: Occupancy) -> list[RentalPaymentObligation]:
    agreement = db.scalar(select(Agreement).where(Agreement.offer_id == occupancy.offer_id))
    scopes = [RentalPaymentObligation.occupancy_id == occupancy.id]
    if agreement is not None:
        scopes.append(RentalPaymentObligation.agreement_id == agreement.id)
    return list(db.scalars(select(RentalPaymentObligation).where(or_(*scopes))))


def _received(obligations: list[RentalPaymentObligation], *, obligation_type: str | None = None) -> float:
    """What the host has on record as received -- confirmed payments only."""
    return _round2(sum(
        float(record.confirmed_amount or 0)
        for obligation in obligations
        if obligation_type is None or obligation.obligation_type == obligation_type
        for record in obligation.records
        if record.status in ("CONFIRMED", "PARTIALLY_PAID")
    ))


def booking_money_summary(db: Session, occupancy: Occupancy) -> dict:
    """What the renter paid on this booking and what's already been sent
    back -- what a new return is checked against."""
    obligations = _booking_obligations(db, occupancy)
    returns = list(db.scalars(select(RentalPaymentReturn).where(RentalPaymentReturn.occupancy_id == occupancy.id)))

    def returned(kind: str) -> float:
        return _round2(sum(
            float(r.amount) + float(r.deductions_amount or 0)
            for r in returns if r.kind == kind and r.status in _COUNTING_STATUSES
        ))

    currency = obligations[0].currency if obligations else ""
    recipient_party_id = obligations[0].recipient_party_id if obligations else None
    # A sublet may route the deposit to a different payee than the rent
    # (ZR-SUBLET-PAY-003 Section 9): the deposit is returned by whoever got it.
    deposit_obligation = next((o for o in obligations if o.obligation_type == "DEPOSIT"), None)
    paid_obligation = next((o for o in sorted(obligations, key=lambda o: o.obligation_type != "DEPOSIT")
                            if _received([o]) > 0), None)
    deposit_paid = _received(obligations, obligation_type="DEPOSIT")
    total_paid = _received(obligations)
    return {
        "recipient_party_id": recipient_party_id,
        "deposit_recipient_party_id": deposit_obligation.recipient_party_id if deposit_obligation else recipient_party_id,
        "deposit_obligation_id": deposit_obligation.id if deposit_obligation else None,
        "paid_obligation_id": paid_obligation.id if paid_obligation else None,
        "paid_recipient_party_ids": sorted({o.recipient_party_id for o in obligations if _received([o]) > 0}),
        "currency": currency,
        "deposit_paid": deposit_paid,
        "total_paid": total_paid,
        "deposit_settled": returned("DEPOSIT_RETURN"),
        "cancellation_settled": returned("CANCELLATION_RETURN"),
    }


def _limit_for(kind: str, summary: dict) -> float:
    if kind == "DEPOSIT_RETURN":
        return _round2(summary["deposit_paid"] - summary["deposit_settled"])
    return _round2(summary["total_paid"] - summary["cancellation_settled"])


def get_return_or_404(db: Session, return_id: int) -> RentalPaymentReturn:
    record = db.get(RentalPaymentReturn, return_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Return not found")
    return record


def record_return(
    db: Session, party: Party, occupancy: Occupancy, *, kind: str, amount: float, deductions_amount: float = 0,
    deductions_reason: str = "", payment_method_category: str, external_reference: str = "",
    returned_date: date, note: str = "", correlation_id: str = "",
) -> RentalPaymentReturn:
    """The host records money they sent back to the renter directly. For a
    deposit, amount + deductions can't exceed the deposit they received (less
    anything already settled); for a cancelled booking, it can't exceed what
    the renter paid on it. A deduction needs a reason -- the renter sees it."""
    if kind not in RENTAL_PAYMENT_RETURN_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"kind must be one of {RENTAL_PAYMENT_RETURN_KINDS}")
    required = _KIND_REQUIRES_OCCUPANCY_STATUS[kind]
    if occupancy.status != required:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A deposit is returned once the tenancy has ended" if kind == "DEPOSIT_RETURN"
            else "This booking hasn't been cancelled",
        )
    if payment_method_category not in RETURN_METHODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"paymentMethodCategory must be one of {RETURN_METHODS}")

    summary = booking_money_summary(db, occupancy)
    allowed = (
        {summary["deposit_recipient_party_id"]} if kind == "DEPOSIT_RETURN"
        else set(summary["paid_recipient_party_ids"]) or {summary["recipient_party_id"]}
    )
    if party.id not in allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the payee who received this money can record its return")

    amount = _round2(amount)
    deductions_amount = _round2(deductions_amount)
    if amount < 0 or deductions_amount < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Amounts can't be negative")
    if amount == 0 and deductions_amount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter the amount you sent back")
    if deductions_amount > 0 and not deductions_reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Explain what was kept back -- the renter sees this")
    limit = _limit_for(kind, summary)
    if amount + deductions_amount > limit + 0.001:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"That's more than there is to settle ({summary['currency']} {max(limit, 0):.2f} "
            + ("of the deposit you received)" if kind == "DEPOSIT_RETURN" else "the renter paid on this booking)"),
        )

    record = RentalPaymentReturn(
        occupancy_id=occupancy.id, kind=kind, status="RECORDED", tenant_guest_id=occupancy.guest_id,
        recipient_party_id=party.id, amount=amount, currency=summary["currency"],
        deductions_amount=deductions_amount, deductions_reason=deductions_reason.strip(),
        payment_method_category=payment_method_category, external_reference=external_reference.strip(),
        returned_date=returned_date, note=note.strip(),
        obligation_id=summary["deposit_obligation_id"] if kind == "DEPOSIT_RETURN" else summary["paid_obligation_id"],
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    log_audit_event(db, None, "rental_payment_return.recorded", "rental_payment_return", str(record.id), correlation_id)
    emit_event(
        db, "rental_payment_return.recorded", "rental_payment_return", str(record.id),
        {"occupancyId": occupancy.id, "kind": kind, "amount": amount, "deductionsAmount": deductions_amount},
        correlation_id=correlation_id, actor_kind="party", actor_id=str(party.id), new_state="RECORDED",
    )
    db.commit()

    what = "your deposit" if kind == "DEPOSIT_RETURN" else "money for your cancelled booking"
    message = f"Your host says they sent back {record.currency} {amount:.2f} of {what}."
    if deductions_amount > 0:
        message += f" They kept {record.currency} {deductions_amount:.2f}: {record.deductions_reason}"
    message += " Confirm it arrived, or report a problem, in Rent & Deposit Payments."
    guest = db.get(Guest, occupancy.guest_id)
    if guest is not None:
        notif_crud.notify_user_by_guest(
            db, guest, title="Money returned by your host", message=message[:2000],
            notification_type="rental_payment_return.recorded",
            related_entity_type="rental_payment_return", related_entity_id=str(record.id),
        )
    return record


def _assert_tenant(record: RentalPaymentReturn, guest: Guest) -> None:
    if record.tenant_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This isn't yours")
    if record.status != "RECORDED":
        raise HTTPException(status.HTTP_409_CONFLICT, "You've already responded to this")


def tenant_confirm_return(db: Session, guest: Guest, record: RentalPaymentReturn, *, correlation_id: str = "") -> RentalPaymentReturn:
    _assert_tenant(record, guest)
    record.status = "CONFIRMED"
    record.tenant_responded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(record)
    emit_event(
        db, "rental_payment_return.confirmed", "rental_payment_return", str(record.id), {},
        correlation_id=correlation_id, actor_kind="guest", actor_id=guest.id, new_state="CONFIRMED",
    )
    db.commit()
    notif_crud.notify_user_by_party(
        db, record.recipient_party_id, title="Return confirmed",
        message=f"The renter confirmed receiving the {record.currency} {float(record.amount):.2f} you sent back.",
        notification_type="rental_payment_return.confirmed",
        related_entity_type="rental_payment_return", related_entity_id=str(record.id),
    )
    return record


def tenant_dispute_return(
    db: Session, guest: Guest, record: RentalPaymentReturn, *, details: str, correlation_id: str = "",
) -> RentalPaymentReturn:
    _assert_tenant(record, guest)
    if not details.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tell your host what's wrong")
    record.status = "DISPUTED"
    record.tenant_dispute_details = details.strip()[:2000]
    record.tenant_responded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(record)
    emit_event(
        db, "rental_payment_return.disputed", "rental_payment_return", str(record.id), {},
        correlation_id=correlation_id, actor_kind="guest", actor_id=guest.id, new_state="DISPUTED",
    )
    db.commit()
    notif_crud.notify_user_by_party(
        db, record.recipient_party_id, title="Problem with money you sent back",
        message=(
            f"The renter reported a problem with the {record.currency} {float(record.amount):.2f} you recorded "
            f"sending back: {record.tenant_dispute_details} -- check it and record it again once it's sorted."
        )[:2000],
        notification_type="rental_payment_return.disputed",
        related_entity_type="rental_payment_return", related_entity_id=str(record.id),
    )
    return record


def list_returns_for_tenant(db: Session, guest_id: str) -> list[RentalPaymentReturn]:
    return list(db.scalars(
        select(RentalPaymentReturn).where(RentalPaymentReturn.tenant_guest_id == guest_id)
        .order_by(RentalPaymentReturn.created_at.desc())
    ))


def list_returns_for_recipient(db: Session, party_id: int) -> list[RentalPaymentReturn]:
    return list(db.scalars(
        select(RentalPaymentReturn).where(RentalPaymentReturn.recipient_party_id == party_id)
        .order_by(RentalPaymentReturn.created_at.desc())
    ))


def list_return_candidates_for_recipient(db: Session, party_id: int) -> list[dict]:
    """The host's ended and cancelled bookings where they received money and
    something may still be owed back -- what the "Returns" screen lists."""
    candidates = []
    # Bookings whose rent/deposit is payable to this host -- recurring rent is
    # linked to the occupancy, the first rent/deposit to the agreement.
    via_occupancy = select(RentalPaymentObligation.occupancy_id).where(
        RentalPaymentObligation.recipient_party_id == party_id, RentalPaymentObligation.occupancy_id.is_not(None),
    )
    via_agreement = (
        select(Occupancy.id)
        .join(Agreement, Agreement.offer_id == Occupancy.offer_id)
        .join(RentalPaymentObligation, RentalPaymentObligation.agreement_id == Agreement.id)
        .where(RentalPaymentObligation.recipient_party_id == party_id)
    )
    occupancies = db.scalars(
        select(Occupancy).where(
            Occupancy.status.in_(("ENDED", "CANCELLED")),
            or_(Occupancy.id.in_(via_occupancy), Occupancy.id.in_(via_agreement)),
        ).order_by(Occupancy.id.desc())
    ).all()
    for occupancy in occupancies:
        summary = booking_money_summary(db, occupancy)
        if summary["recipient_party_id"] != party_id:
            continue
        kind = "DEPOSIT_RETURN" if occupancy.status == "ENDED" else "CANCELLATION_RETURN"
        paid = summary["deposit_paid"] if kind == "DEPOSIT_RETURN" else summary["total_paid"]
        if paid <= 0:
            continue
        candidates.append({
            "occupancy_id": occupancy.id, "kind": kind, "listing_name": occupancy.listing.name if occupancy.listing else "",
            "occupancy_status": occupancy.status, "currency": summary["currency"], "paid": paid,
            "settled": summary["deposit_settled"] if kind == "DEPOSIT_RETURN" else summary["cancellation_settled"],
            "remaining": max(_limit_for(kind, summary), 0.0), "ended_on": occupancy.move_out_date,
        })
    return candidates
