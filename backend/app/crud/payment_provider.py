"""ZR-ENG-CLR-005 Section 3.1/9.1/15.1: the payment-provider dispatch +
callback ingestion pipeline. dispatch_payment_to_provider calls the real
Stripe API (app/services/stripe_client.py) when settings.stripe_secret_key
is configured -- otherwise it falls back to exactly the pre-existing
simulated behavior (a generated id, no network call), so every existing
test and every dev environment without real credentials keeps working
unchanged. The /payments/provider-callback/simulate route stays available
either way, as an authenticated admin action standing in for a real
webhook -- same role as crud/signature_provider.py's own simulate route --
while /payments/stripe/webhook is the real, signature-verified, public
endpoint Stripe itself calls once real keys are configured.

This is additive, not a replacement: crud/finance.py's create_payment_intent/
confirm_payment still serve the direct/synchronous path for a genuinely
EXTERNAL (off-platform, self-attested) payment -- the same role SIMPLE_ESIGN's
direct /sign endpoint plays alongside signature dispatch/callback. dispatch_
payment_to_provider itself now flips the payment's method_class to a real PSP
class the moment it's dispatched (AC-27), which is what then forces it through
THIS module's own webhook-driven ingest_provider_callback below rather than a
direct confirm call -- dispatch/ingest/reconcile are the seam a real PSP
integration actually uses: dispatch_payment_to_provider is where the real
adapter calls out to the PSP's API, ingest_provider_callback is where the
real signed webhook lands.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.ids import new_id
from app.models.admin_user import AdminUser
from app.services import stripe_client
from app.models.finance import (
    PaymentProviderEvent,
    PaymentProviderStatus,
    ProcessorTransaction,
    SimulatedPayment,
)
from app.schemas.finance import PaymentAllocationInput


def get_or_create_provider_status(db: Session) -> PaymentProviderStatus:
    row = db.get(PaymentProviderStatus, 1)
    if row is None:
        row = PaymentProviderStatus(id=1, healthy=True)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def set_provider_health(db: Session, admin: AdminUser, healthy: bool) -> PaymentProviderStatus:
    """super_admin-only -- flips the simulated provider between healthy and
    an outage, so 'recover from provider outage' behavior is actually
    exercisable, not just asserted in a docstring."""
    row = get_or_create_provider_status(db)
    row.healthy = healthy
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def get_processor_transaction_or_404(db: Session, transaction_id: int) -> ProcessorTransaction:
    txn = db.get(ProcessorTransaction, transaction_id)
    if not txn:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Processor transaction not found")
    return txn


def dispatch_payment_to_provider(
    db: Session, payment: SimulatedPayment, admin: AdminUser, allocations: list[PaymentAllocationInput],
) -> ProcessorTransaction:
    """PENDING payment -> creates the ProcessorTransaction representing the
    attempt sent to the (simulated) provider. Fails closed (503) while the
    provider is marked unhealthy -- never falls back to recording the
    payment as confirmed anyway. Only one live (PENDING) dispatch may exist
    per payment at a time, same single-active-attempt discipline as
    signature_provider's one-PENDING-request-per-signer shape."""
    if payment.status != "PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a PENDING payment can be dispatched to the provider")
    existing_pending = (
        db.query(ProcessorTransaction)
        .filter(ProcessorTransaction.payment_id == payment.id, ProcessorTransaction.status == "PENDING")
        .first()
    )
    if existing_pending is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A dispatch is already pending for this payment")

    provider_status = get_or_create_provider_status(db)
    if not provider_status.healthy:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Payment provider is currently unavailable")

    if not allocations:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one allocation is required to dispatch a payment")

    provider_transaction_id = stripe_client.create_payment_intent(
        amount=float(payment.amount), currency=payment.currency,
        metadata={"payment_id": str(payment.id), "idempotency_key": payment.idempotency_key},
    )

    now = datetime.now(timezone.utc)
    txn = ProcessorTransaction(
        payment_id=payment.id,
        provider_transaction_id=provider_transaction_id,
        status="PENDING",
        declared_allocations={str(a.obligation_id): float(a.amount) for a in allocations},
        dispatch_deadline=now + timedelta(minutes=settings.payment_provider_dispatch_timeout_minutes),
    )
    db.add(txn)
    # ZR-ENG-CLR-005 AC-27/Section 12.1: from this point on, this payment is
    # asserting a real PSP rail handled it -- crud/finance.py:confirm_payment's
    # own gate then refuses to let a direct (non-super-admin) confirm call
    # complete it; only this dispatch's own webhook-driven
    # ingest_provider_callback below may. "CARD" is this build's one real
    # Stripe integration's actual rail (stripe_client.create_payment_intent
    # never requests a specific payment_method_types set), not a claim that
    # every future PSP dispatch will be a card.
    payment.method_class = "CARD"
    db.commit()
    db.refresh(txn)
    return txn


def ingest_provider_callback(
    db: Session, admin: AdminUser, *, provider_event_id: str, provider_transaction_id: str, event_type: str,
) -> ProcessorTransaction:
    """'Authenticated, replay-protected and idempotent': provider_event_id is
    DB-unique -- a duplicate/replayed callback loses the IntegrityError race
    and is treated as already-processed, never reprocessed (same
    SAVEPOINT+IntegrityError idiom as
    crud/signature_provider.py:ingest_provider_callback). PAYMENT_SUCCEEDED
    routes through the exact same crud/finance.py:confirm_payment every
    other entrypoint uses, applying only the allocations declared at
    dispatch time -- never allocations supplied with the callback itself,
    since a real PSP webhook has no way to know Zoiko's internal Obligation
    rows."""
    from app.crud import finance as finance_crud
    from app.schemas.finance import PaymentConfirm

    if event_type not in ("PAYMENT_SUCCEEDED", "PAYMENT_FAILED"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "eventType must be PAYMENT_SUCCEEDED or PAYMENT_FAILED")

    txn = db.query(ProcessorTransaction).filter(
        ProcessorTransaction.provider_transaction_id == provider_transaction_id,
    ).first()
    if not txn:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No processor transaction matches that provider transaction id")

    try:
        with db.begin_nested():
            db.add(PaymentProviderEvent(
                provider_event_id=provider_event_id, processor_transaction_id=txn.id, event_type=event_type,
                processed_at=datetime.now(timezone.utc),
            ))
            db.flush()
    except IntegrityError:
        # Already-seen event id -- idempotent no-op, not an error.
        db.refresh(txn)
        return txn

    if txn.status != "PENDING":
        # Already resolved by an earlier event (or reconciliation) -- the
        # dedup above only catches a replayed *event id*; a genuinely new
        # event id arriving for an already-settled transaction is a no-op too.
        return txn

    if event_type == "PAYMENT_FAILED":
        txn.status = "FAILED"
        txn.completed_at = datetime.now(timezone.utc)
        payment = db.get(SimulatedPayment, txn.payment_id)
        payment.status = "FAILED"
        db.commit()
        db.refresh(txn)
        return txn

    # PAYMENT_SUCCEEDED: complete via the same confirm_payment every other
    # path uses, with exactly the allocations declared at dispatch time.
    payment = db.get(SimulatedPayment, txn.payment_id)
    allocations = [
        PaymentAllocationInput(obligation_id=int(obligation_id), amount=amount)
        for obligation_id, amount in txn.declared_allocations.items()
    ]
    finance_crud.confirm_payment(db, payment, PaymentConfirm(allocations=allocations), admin)
    txn.status = "SUCCEEDED"
    txn.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(txn)
    return txn


def reconcile_stalled_payments(db: Session) -> list[ProcessorTransaction]:
    """Manual sweep (same on-demand pattern as services/booking_expiry.py's
    sweep_* functions -- no scheduler exists in this stack). Anything still
    PENDING past its own dispatch deadline while the provider is unhealthy
    is marked FAILED for admin/finance review -- never auto-completed, and
    never silently retried."""
    provider_status = get_or_create_provider_status(db)
    if provider_status.healthy:
        return []

    now = datetime.now(timezone.utc)
    stalled = db.query(ProcessorTransaction).filter(
        ProcessorTransaction.status == "PENDING",
        ProcessorTransaction.dispatch_deadline.is_not(None),
        ProcessorTransaction.dispatch_deadline <= now,
    ).all()
    for txn in stalled:
        txn.status = "FAILED"
        txn.completed_at = now
        payment = db.get(SimulatedPayment, txn.payment_id)
        payment.status = "FAILED"
    if stalled:
        db.commit()
    return stalled


# Real Stripe event types this module understands -> our own PAYMENT_SUCCEEDED/
# PAYMENT_FAILED vocabulary. Anything else is ignored (2xx'd back to Stripe
# without action) rather than erroring -- Stripe's account sends many event
# types this integration doesn't need to react to, and erroring on them
# would make Stripe keep retrying an event we were never going to act on.
STRIPE_EVENT_TYPE_MAP = {
    "payment_intent.succeeded": "PAYMENT_SUCCEEDED",
    "payment_intent.payment_failed": "PAYMENT_FAILED",
}


def get_system_admin(db: Session) -> AdminUser:
    """The identity attributed to actions a real Stripe webhook triggers --
    there is no human session behind an incoming webhook call, so this
    resolves the platform's own seed super_admin (settings.seed_admin_email)
    as a system actor, the same way an automated sweep or scheduled job
    would need one. Only ever used for the unauthenticated, public
    /payments/stripe/webhook route -- every other entrypoint here keeps
    requiring a real authenticated admin session."""
    from app.crud.admin import get_admin_by_email

    admin = get_admin_by_email(db, settings.seed_admin_email)
    if admin is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "No system admin account is configured")
    return admin


def ingest_stripe_webhook_event(db: Session, event) -> ProcessorTransaction | None:
    """Maps a real, already signature-verified Stripe Event to
    ingest_provider_callback. Returns None (a no-op) for any event type
    this integration doesn't act on."""
    event_type = STRIPE_EVENT_TYPE_MAP.get(event["type"])
    if event_type is None:
        return None
    admin = get_system_admin(db)
    return ingest_provider_callback(
        db, admin, provider_event_id=event["id"], provider_transaction_id=event["data"]["object"]["id"],
        event_type=event_type,
    )
