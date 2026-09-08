"""ZR-ENG-CLR-001 Section 1, Rule 7 / Section 11.3: the Booking Service's
acceptance-confirmation expiry clock.

Scope note: this covers only the 24-hour accepted-booking confirmation
window (10.1) -- the offer/agreement/move-in pipeline's equivalent of
"ACCEPTED_AWAITING_CONFIRMATION". The 30-minute active payment checkout lock
(10.2, PAYMENT_IN_PROGRESS/PAYMENT_PENDING) is deliberately NOT built here:
there is no real payment checkout session in this codebase to attach it to
(Obligation/SimulatedPayment is a manual admin-recorded ledger, not a live
payment-provider flow), and Section 5 (payment/settlement rules) is
explicitly out of scope for Section 1 -- inventing checkout-lock semantics
now would mean inventing Section 5 business rules early, which the spec's
own implementation guardrail (Section 2) forbids.

No job scheduler exists in this stack (see crud/occupancy.py's own docstring
on the same limitation for rent generation). Consistent with that existing
pattern, expiry here is lazy (checked whenever an accepted offer is read or
acted on -- self-healing, no missed tick can cause incorrect behavior) plus
an on-demand bulk sweep for admin/ops use until a real scheduler exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.audit import log_audit_event
from app.models.leasing import Offer
from app.services import inventory as inventory_service


def compute_confirmation_deadline(accepted_at: datetime) -> datetime:
    return accepted_at + timedelta(hours=settings.offer_acceptance_confirmation_hours)


def is_offer_overdue(offer: Offer, *, now: datetime | None = None) -> bool:
    """True only for an offer still sitting in ACCEPTED past its own
    confirmation_expires_at -- an offer that has since moved on (agreement
    created, declined, already expired) is never "overdue" again."""
    if offer.status != "ACCEPTED" or offer.confirmation_expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now >= offer.confirmation_expires_at


def expire_offer_if_overdue(
    db: Session, offer: Offer, *, now: datetime | None = None, correlation_id: str = "",
) -> bool:
    """Lazy expiry: called at the start of any operation that reads or acts
    on an ACCEPTED offer. Returns True if it just expired the offer (the
    caller should treat it as no longer ACCEPTED), False if there was
    nothing to do. Idempotent -- safe to call on every access."""
    if not is_offer_overdue(offer, now=now):
        return False

    offer.status = "EXPIRED"
    inventory_service.release_hold(
        db, source_type="offer", source_id=offer.id, reason="acceptance_window_expired",
        correlation_id=correlation_id,
    )
    # Section 15: every automated state change gets a complete audit event --
    # actor=None marks this as system-initiated, not an admin action.
    log_audit_event(
        db, None, "offer.expire", "offer", str(offer.id), correlation_id,
        reason="acceptance_window_expired",
    )
    return True


def sweep_expired_offers(db: Session, *, correlation_id: str = "") -> list[Offer]:
    """Manual substitute for a cron tick (no scheduler exists in this stack
    -- see module docstring): expires every ACCEPTED offer past its
    confirmation deadline in one pass. Callable on demand by an admin action
    today; the natural hook for a real scheduler later."""
    now = datetime.now(timezone.utc)
    candidates = db.scalars(
        select(Offer).where(Offer.status == "ACCEPTED", Offer.confirmation_expires_at.is_not(None), Offer.confirmation_expires_at <= now)
    ).all()
    expired = []
    for offer in candidates:
        if expire_offer_if_overdue(db, offer, now=now, correlation_id=correlation_id):
            expired.append(offer)
    if expired:
        db.commit()
    return expired
