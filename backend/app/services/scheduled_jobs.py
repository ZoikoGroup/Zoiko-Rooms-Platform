"""The platform's recurring work, run hourly by the in-process scheduler in
app/main.py (and on demand by a super admin). Rent is paid to hosts
directly and recording it is optional, so nothing here can wait for a
payment to happen first -- each month's rent is created on its own
schedule, statuses move from UPCOMING to DUE to OVERDUE as dates pass, and
both sides get reminded.

Every job is idempotent: running it twice, or on two servers at once, never
creates a duplicate obligation or a second copy of the same reminder. One
job failing is logged and rolled back without stopping the others."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import notification as notif_crud
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.rental_payment import RentalPaymentObligation, RentalPaymentRecord

logger = logging.getLogger("uvicorn.error")

# Upper bound on months created for one occupancy in one run -- catches up a
# tenancy that fell behind without ever looping unbounded.
MAX_RENT_PERIODS_PER_RUN = 24


def _already_notified(db: Session, notification_type: str, entity_type: str, entity_id: int) -> bool:
    return db.scalar(
        select(Notification.id).where(
            Notification.notification_type == notification_type,
            Notification.related_entity_type == entity_type,
            Notification.related_entity_id == str(entity_id),
        ).limit(1)
    ) is not None


def expire_overdue_bookings(db: Session) -> int:
    """Offers past their confirmation window and signed bookings past their
    payment deadline (settings.direct_payment_confirmation_hold_days) --
    releases the room hold so it can be booked again."""
    from app.services.booking_expiry import sweep_expired_checkouts, sweep_expired_offers

    return len(sweep_expired_offers(db)) + len(sweep_expired_checkouts(db))


def _latest_rent_due_date(occupancy: Occupancy) -> date | None:
    """Same two places crud/occupancy.py:generate_next_rent_obligation looks:
    the first rent is linked to the agreement, later ones to the occupancy."""
    agreement = occupancy.offer.agreement if occupancy.offer else None
    obligations = list(occupancy.obligations) + (list(agreement.obligations) if agreement else [])
    due_dates = [o.due_date for o in obligations if o.obligation_type == "RENT"]
    return max(due_dates) if due_dates else None


def generate_due_rent(db: Session, *, today: date | None = None) -> int:
    """Creates each active tenancy's next rent period once the latest one's
    due date has arrived -- whether or not that one was recorded as paid.
    generate_next_rent_obligation itself refuses duplicates and stops at the
    lease end, so this only decides *when* to ask."""
    from app.crud.occupancy import generate_next_rent_obligation

    today = today or datetime.now(timezone.utc).date()
    created = 0
    occupancies = db.scalars(select(Occupancy).where(Occupancy.status == "ACTIVE")).all()
    for occupancy in occupancies:
        for _ in range(MAX_RENT_PERIODS_PER_RUN):
            latest = _latest_rent_due_date(occupancy)
            if latest is None or latest > today:
                break
            try:
                obligation = generate_next_rent_obligation(db, occupancy, admin=None)
            except HTTPException:
                break
            if obligation is None:
                break
            created += 1
            db.expire(occupancy)
    return created


def refresh_due_statuses(db: Session, *, today: date | None = None) -> int:
    """UPCOMING -> DUE -> OVERDUE only happens when something recomputes an
    obligation -- this recomputes every open one whose date has come, and
    tells both sides once when rent goes overdue."""
    from app.crud.rental_payment import recompute_obligation_status

    today = today or datetime.now(timezone.utc).date()
    candidates = db.scalars(
        select(RentalPaymentObligation).where(
            RentalPaymentObligation.status.in_(("UPCOMING", "DUE")),
            RentalPaymentObligation.due_date <= today,
        )
    ).all()
    changed = 0
    for obligation in candidates:
        before = obligation.status
        recompute_obligation_status(db, obligation)
        if obligation.status == before:
            continue
        changed += 1
        if obligation.status == "OVERDUE" and not _already_notified(
            db, "rental_payment.overdue", "rental_payment_obligation", obligation.id,
        ):
            label = obligation.display_label
            amount = f"{obligation.currency} {obligation.outstanding_amount:.2f}"
            notif_crud.notify_user_by_guest(
                db, obligation.tenant, title=f"{label.title()} overdue",
                message=(
                    f"Your {label} of {amount} was due on {obligation.due_date.isoformat()}. Pay your host directly "
                    "using the payment instructions, then record the payment."
                ),
                notification_type="rental_payment.overdue",
                related_entity_type="rental_payment_obligation", related_entity_id=str(obligation.id),
            )
            notif_crud.notify_user_by_party(
                db, obligation.recipient_party_id, title=f"{label.title()} overdue",
                message=(
                    f"A tenant's {label} of {amount} was due on {obligation.due_date.isoformat()} and hasn't been "
                    "recorded yet. If you've received it, mark it as received in Payments."
                ),
                notification_type="rental_payment.overdue",
                related_entity_type="rental_payment_obligation", related_entity_id=str(obligation.id),
            )
    db.commit()
    return changed


def send_due_soon_reminders(db: Session) -> int:
    from app.services.rental_payment_due_soon import sweep_rental_payment_due_soon

    return len(sweep_rental_payment_due_soon(db))


def reconcile_listing_fee_refunds(db: Session) -> int:
    """Listing Fee refunds stuck in PROCESSING are checked with Stripe (in case
    their webhook never arrived) -- succeeded ones confirmed, failed ones marked
    FAILED so they can be retried."""
    from app.crud.listing_fee import reconcile_processing_refunds

    return reconcile_processing_refunds(db)


def remind_hosts_of_unconfirmed_payments(db: Session, *, now: datetime | None = None) -> int:
    """A renter recorded a direct payment and the host hasn't confirmed or
    questioned it after settings.payment_confirmation_reminder_days -- nudge
    the host once per record."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.payment_confirmation_reminder_days)
    records = db.scalars(
        select(RentalPaymentRecord).where(
            RentalPaymentRecord.status == "PAYER_RECORDED",
            RentalPaymentRecord.created_at <= cutoff,
        )
    ).all()
    reminded = 0
    for record in records:
        if _already_notified(db, "rental_payment.confirmation_reminder", "rental_payment_record", record.id):
            continue
        obligation = record.obligation
        notif_crud.notify_user_by_party(
            db, obligation.recipient_party_id, title="Payment waiting for your confirmation",
            message=(
                f"A tenant recorded paying {record.declared_currency} {float(record.declared_amount):.2f} for "
                f"{obligation.display_label} on {record.declared_date.isoformat()}. Check you received it, then "
                "confirm it -- or report a problem -- in Payments."
            ),
            notification_type="rental_payment.confirmation_reminder",
            related_entity_type="rental_payment_record", related_entity_id=str(record.id),
        )
        reminded += 1
    if reminded:
        db.commit()
    return reminded


JOBS: tuple[tuple[str, Callable[[Session], int]], ...] = (
    ("expire_overdue_bookings", expire_overdue_bookings),
    ("generate_due_rent", generate_due_rent),
    ("refresh_due_statuses", refresh_due_statuses),
    ("send_due_soon_reminders", send_due_soon_reminders),
    ("remind_hosts_of_unconfirmed_payments", remind_hosts_of_unconfirmed_payments),
    ("reconcile_listing_fee_refunds", reconcile_listing_fee_refunds),
)


def run_scheduled_jobs(db: Session) -> dict[str, int | str]:
    """Runs every job in order; returns what each one did (a count, or
    "error" if it failed). A failure is rolled back and logged -- it never
    stops the jobs after it."""
    results: dict[str, int | str] = {}
    for name, job in JOBS:
        try:
            results[name] = job(db)
        except Exception:
            db.rollback()
            logger.exception("scheduled job %s failed", name)
            results[name] = "error"
    return results


# Any fixed number, the same on every server: pg_try_advisory_lock makes a
# run on one server skip while another server is mid-run.
_ADVISORY_LOCK_KEY = 7_425_310_001


def run_scheduled_jobs_once() -> dict[str, int | str] | None:
    """One scheduler tick with its own session. Returns None when another
    server already holds the run lock (Postgres only; other databases have
    no second server to race)."""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    db = SessionLocal()
    locked = False
    try:
        if db.get_bind().dialect.name == "postgresql":
            locked = bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _ADVISORY_LOCK_KEY}).scalar())
            db.commit()
            if not locked:
                return None
        return run_scheduled_jobs(db)
    finally:
        if locked:
            try:
                db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _ADVISORY_LOCK_KEY})
                db.commit()
            except Exception:
                logger.exception("scheduled jobs: could not release the run lock")
        db.close()
