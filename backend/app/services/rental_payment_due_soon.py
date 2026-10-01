"""ZR-PAY-002 Section 14: 'Payment due / approaching -> Tenant -- State
obligation, due date and authorized recipient; never imply Zoiko Rooms is
payee.' No job scheduler exists in this stack (see
services/verification_followups.py's own docstring on the same limitation)
-- this is the same on-demand bulk sweep shape, run manually or wired behind
an admin action until a real scheduler exists."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.events import emit_event
from app.models.rental_payment import RentalPaymentObligation

# How far ahead of due_date to raise the reminder. Not specified as a fixed
# number by ZR-PAY-002 (which leaves lead time to product/jurisdiction
# policy) -- a reasonable default, same "not a verified figure" honesty as
# services/verification_followups.py:FOLLOW_UP_REMINDER_LEAD_DAYS.
PAYMENT_DUE_SOON_LEAD_DAYS = 3


def sweep_rental_payment_due_soon(db: Session, *, today: date | None = None) -> list[RentalPaymentObligation]:
    """Notifies the tenant once per obligation when its due_date falls
    within PAYMENT_DUE_SOON_LEAD_DAYS (including already-overdue ones with
    no declaration yet) -- idempotent via due_soon_notified_at. Only
    UPCOMING/DUE/OVERDUE obligations qualify: once a tenant has declared a
    payment (or it's been waived/cancelled/confirmed/disputed), a 'this is
    due soon' reminder no longer applies."""
    today = today or datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=PAYMENT_DUE_SOON_LEAD_DAYS)

    candidates = db.scalars(
        select(RentalPaymentObligation).where(
            RentalPaymentObligation.status.in_(("UPCOMING", "DUE", "OVERDUE")),
            RentalPaymentObligation.due_date <= horizon,
            RentalPaymentObligation.due_soon_notified_at.is_(None),
        )
    ).all()

    notified: list[RentalPaymentObligation] = []
    now = datetime.now(timezone.utc)
    for obligation in candidates:
        # ZR-PAY-002 Section 10: 'must not assume ... a universal legal
        # meaning for "deposit"' -- display_label resolves DEPOSIT to
        # whatever this jurisdiction actually calls it.
        label = obligation.display_label
        notif_crud.notify_user_by_guest(
            db, obligation.tenant,
            title=f"{label.title()} payment due",
            message=(
                f"Your {label} payment of {obligation.currency} "
                f"{float(obligation.amount):.2f} is due on {obligation.due_date.isoformat()}. This payment is made "
                "directly to your landlord or agent -- Zoiko Rooms does not receive or hold this payment."
            ),
            notification_type="rental_payment.due",
            related_entity_type="rental_payment_obligation", related_entity_id=str(obligation.id),
        )
        obligation.due_soon_notified_at = now
        _email_due_soon(db, obligation)
        emit_event(
            db, "rental_payment.due", "rental_payment_obligation", str(obligation.id),
            {"dueDate": obligation.due_date.isoformat(), "amount": float(obligation.amount), "currency": obligation.currency},
        )
        notified.append(obligation)

    if notified:
        db.commit()
    return notified


def _email_due_soon(db: Session, obligation: RentalPaymentObligation) -> None:
    """ZR-EML-RENT-001 for rent, ZR-EML-PAY-001 for anything else (e.g. the
    deposit). Best effort -- never stops the sweep."""
    import logging

    from app.core.mailer import send_payment_due_email, send_rent_reminder_email
    from app.crud.guest import get_user_for_guest
    from app.crud.user import get_user_by_party_id
    from app.models.finance import AutopayMandate

    try:
        tenant = get_user_for_guest(db, obligation.tenant) if obligation.tenant else None
        if tenant is None:
            return
        host = get_user_by_party_id(db, obligation.recipient_party_id)
        payee = f"{host.full_name} (your host), paid directly" if host else "Your host, paid directly"
        listing = None
        if obligation.agreement is not None and obligation.agreement.offer is not None:
            listing = obligation.agreement.offer.listing
        elif obligation.occupancy is not None and obligation.occupancy.offer is not None:
            listing = obligation.occupancy.offer.listing
        room_label = listing.name if listing else "your rental"

        if obligation.obligation_type == "RENT":
            autopay = None
            if obligation.occupancy_id:
                autopay = db.scalar(select(AutopayMandate).where(
                    AutopayMandate.occupancy_id == obligation.occupancy_id, AutopayMandate.status == "ACTIVE",
                ))
            send_rent_reminder_email(
                tenant.email, tenant.full_name, obligation_id=obligation.id, amount=float(obligation.amount),
                currency=obligation.currency, due_date=obligation.due_date, room_label=room_label, payee=payee,
                autopay_status="Active" if autopay else "Not set up",
            )
        else:
            send_payment_due_email(
                tenant.email, tenant.full_name, obligation_id=obligation.id,
                purpose=f"the {obligation.display_label} for {room_label}", amount=float(obligation.amount),
                currency=obligation.currency, due_date=obligation.due_date, payee=payee,
            )
    except Exception:
        logging.getLogger("uvicorn.error").exception("due-soon email failed for obligation %s", obligation.id)
