"""ZR-ENG-CLR-001 Section 1, Rule 7 / Section 11.3: the Booking Service's two
independent expiry clocks -- the 24-hour accepted-booking confirmation window
(10.1, Offer.ACCEPTED -> EXPIRED) and the 30-minute active payment checkout
lock (10.2, Agreement.PAYMENT_IN_PROGRESS). The offer/agreement/move-in
pipeline's equivalent of ACCEPTED_AWAITING_CONFIRMATION is Offer.ACCEPTED;
PAYMENT_IN_PROGRESS is the equivalent of the spec's payment-checkout state,
entered once both signatures land on an Agreement (see
crud/leasing.py:_apply_signature) and exited only via
crud/leasing.py:confirm_agreement_payment once every initial obligation
clears, or via expire_checkout_if_overdue below on timeout.

No job scheduler exists in this stack (see crud/occupancy.py's own docstring
on the same limitation for rent generation). Consistent with that existing
pattern, expiry here is lazy (checked whenever an accepted offer/agreement is
read or acted on -- self-healing, no missed tick can cause incorrect
behavior) plus an on-demand bulk sweep for admin/ops use until a real
scheduler exists (see reconcile_expirations.py, run as a standalone script the
same way check_alerts.py already is).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.leasing import Agreement, Offer
from app.models.listing_approval import CURRENT_POLICY_VERSION
from app.models.market_release import MarketRelease
from app.services import inventory as inventory_service
from app.services.policy import get_policy


def compute_confirmation_deadline(accepted_at: datetime, market_release: MarketRelease | None = None) -> datetime:
    """Duration is config-driven per Section 14 policy key
    booking.acceptance_hold_duration -- market_release, when given, may
    override the platform-wide default (see services/policy.py)."""
    hours = get_policy(market_release, "booking.acceptance_hold_duration_hours")
    return accepted_at + timedelta(hours=hours)


def compute_checkout_deadline(started_at: datetime, market_release: MarketRelease | None = None) -> datetime:
    """Duration is config-driven per Section 14 policy key
    payment.checkout_lock_duration."""
    minutes = get_policy(market_release, "payment.checkout_lock_duration_minutes")
    return started_at + timedelta(minutes=minutes)


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

    before_state = offer.status
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
        before_state=before_state, after_state=offer.status, policy_version=CURRENT_POLICY_VERSION,
    )

    # ZR-ENG-CLR-004 4.5 'What happens if one party does not sign?': "the
    # booking can remain in its time-limited confirmation state until the
    # applicable signing deadline expires... the incomplete agreement version
    # is closed as NOT_EXECUTED/EXPIRED and preserved for audit." This
    # codebase's signing deadline IS the offer's own 24h confirmation
    # window -- an agreement created off this offer but still short of both
    # signatures expires alongside it. Signatures already collected (if any)
    # are left in place, never cleared -- they're evidence, not undone.
    # Deliberately scoped to SENT/PARTIALLY_EXECUTED only -- once both
    # signatures land (PAYMENT_IN_PROGRESS/PAYMENT_PENDING),
    # expire_checkout_if_overdue below owns that agreement's own expiry
    # outcome (SENT-revert or VOID), so the two lazy checks never race to
    # assign the same agreement two different terminal state names.
    agreement = offer.agreement
    if agreement is not None and agreement.status in ("SENT", "PARTIALLY_EXECUTED"):
        agreement_before_state = agreement.status
        agreement.status = "EXPIRED"
        agreement.payment_session_expires_at = None
        log_audit_event(
            db, None, "agreement.expire", "agreement", str(agreement.id), correlation_id,
            reason="signing_deadline_expired",
            before_state=agreement_before_state, after_state=agreement.status, policy_version=CURRENT_POLICY_VERSION,
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


def is_checkout_overdue(agreement: Agreement, *, now: datetime | None = None) -> bool:
    """True only for an agreement still sitting in PAYMENT_IN_PROGRESS past its
    own payment_session_expires_at -- one that has since cleared payment
    (SIGNED) or gone anywhere else is never 'overdue' again."""
    if agreement.status != "PAYMENT_IN_PROGRESS" or agreement.payment_session_expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now >= agreement.payment_session_expires_at


def expire_checkout_if_overdue(
    db: Session, agreement: Agreement, *, now: datetime | None = None, correlation_id: str = "",
) -> bool:
    """Lazy checkout-lock expiry (10.2). Two outcomes, mirroring the spec
    exactly: if the offer's own 24h acceptance window is still open, the
    agreement reverts to SENT so the renter can retry checkout with the room
    hold still intact ('return to accepted state if the commercial hold is
    still valid'); otherwise there's nothing left to hold the room for, and
    the agreement is left VOID (this codebase's terminal non-signed state --
    the room hold itself is released separately, via the Offer's own lazy
    expiry, since RoomHold is keyed off the Offer, not the Agreement)."""
    if not is_checkout_overdue(agreement, now=now):
        return False

    now = now or datetime.now(timezone.utc)
    before_state = agreement.status
    offer = agreement.offer
    acceptance_hold_still_valid = bool(
        offer and offer.confirmation_expires_at and now < offer.confirmation_expires_at
    )
    agreement.payment_session_expires_at = None
    if acceptance_hold_still_valid:
        agreement.status = "SENT"
        reason = "checkout_window_expired_acceptance_hold_still_valid"
    else:
        agreement.status = "VOID"
        reason = "checkout_and_acceptance_window_both_expired"

    log_audit_event(
        db, None, "agreement.checkout_expire", "agreement", str(agreement.id), correlation_id, reason=reason,
        before_state=before_state, after_state=agreement.status, policy_version=CURRENT_POLICY_VERSION,
    )
    return True


def sweep_expired_checkouts(db: Session, *, correlation_id: str = "") -> list[Agreement]:
    """Manual substitute for a cron tick, same shape as sweep_expired_offers."""
    now = datetime.now(timezone.utc)
    candidates = db.scalars(
        select(Agreement).where(
            Agreement.status == "PAYMENT_IN_PROGRESS",
            Agreement.payment_session_expires_at.is_not(None),
            Agreement.payment_session_expires_at <= now,
        )
    ).all()
    expired = []
    for agreement in candidates:
        if expire_checkout_if_overdue(db, agreement, now=now, correlation_id=correlation_id):
            expired.append(agreement)
    if expired:
        db.commit()
    return expired
