"""ZR-PAY-LINK-003 Section 6/10.1/Wireframe F: the short-lived
provider-hosted payment handoff. create_session starts one; resolve_session
is the return-page self-heal read; record_provider_payment_success/_failure
are what both the webhook and resolve_session ultimately call to reconcile
it. This is the first real code path producing
RentalPaymentRecord(provenance="PROVIDER_CONFIRMATION") --
models/rental_payment.py's own RENTAL_PAYMENT_PROVENANCE docstring flagged
this as modeled-but-unbacked before this module existed."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.events import emit_event
from app.crud.rental_payment_provider_account import get_charge_ready_provider_account_for_party
from app.models.external_payment_session import ExternalPaymentSession, RentalPaymentProviderEvent
from app.models.guest import Guest
from app.models.rental_payment import RentalPaymentObligation, RentalPaymentRecord
from app.models.rental_payment_provider_account import RentalPaymentProviderAccount
from app.services import stripe_client

# Only these two Stripe event types are ever meaningful here -- everything
# else this platform's shared webhook endpoint might see (e.g. a Listing
# Fee event landing on the wrong secret in a misconfigured Stripe dashboard)
# is silently ignored, same posture as
# crud/listing_fee.py:STRIPE_EVENT_TYPE_MAP.
STRIPE_EVENT_TYPE_MAP = {
    "checkout.session.completed": "CHECKOUT_SESSION_COMPLETED",
    "checkout.session.async_payment_failed": "CHECKOUT_SESSION_ASYNC_PAYMENT_FAILED",
}


def _round2(amount) -> float:
    return round(float(amount), 2)


def get_session_by_checkout_session_id_or_404(db: Session, checkout_session_id: str) -> ExternalPaymentSession:
    """The return leg once Stripe redirects the tenant back from its own
    hosted page -- same role as
    crud/listing_fee.py:get_payment_by_checkout_session_id plays for the
    Listing Fee's own return leg."""
    session = db.scalar(select(ExternalPaymentSession).where(ExternalPaymentSession.provider_checkout_session_id == checkout_session_id))
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment session not found")
    return session


def get_session_or_404(db: Session, session_id: int) -> ExternalPaymentSession:
    session = db.get(ExternalPaymentSession, session_id)
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment session not found")
    return session


def get_latest_session_for_obligation(db: Session, obligation_id: int) -> ExternalPaymentSession | None:
    """The one query crud/rental_payment.py:recompute_obligation_status
    goes through to decide PAYMENT_SESSION_STARTED (ZR-PAY-LINK-003 Section
    16) -- never re-derived inline, same discipline every other *_for_room/
    *_for_party resolver in this codebase already follows."""
    return db.scalar(
        select(ExternalPaymentSession)
        .where(ExternalPaymentSession.obligation_id == obligation_id)
        .order_by(ExternalPaymentSession.created_at.desc())
    )


def _require_market_approved_handoff(db: Session, jurisdiction_code: str) -> None:
    """ZR-PAY-CFG-001 5.1: an external recipient-owned payment link opens
    only where that handoff has been separately approved for the market."""
    from app.models.market_release import MarketRelease
    from app.services.policy import get_policy

    release = db.scalar(select(MarketRelease).where(MarketRelease.jurisdiction == jurisdiction_code))
    if not get_policy(release, "payment.external_handoff_approved"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Online payment to the recipient isn't available in this market. Use the payment instructions to pay "
            "the recipient directly, then record the payment.",
        )


def create_session(
    db: Session, guest: Guest, obligation: RentalPaymentObligation, *, success_url: str, cancel_url: str,
    correlation_id: str = "",
) -> tuple[ExternalPaymentSession, str]:
    """ZR-PAY-LINK-003 Section 19 POST /rental-payment-obligations/{id}/payment-session.
    Requires a charge-ready provider account for the obligation's own
    recipient -- Wireframe C: 'No rental payment can start while recipient
    onboarding, capability... are incomplete.' Returns (session, checkout_url)
    -- same shape as crud/listing_fee.py:create_checkout, for the same
    reason: Stripe's own checkout_url is never persisted (it's only ever
    needed once, for this same redirect), so it's returned rather than
    stored."""
    if obligation.tenant_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This obligation does not belong to you")
    if obligation.status in ("WAIVED", "CANCELLED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This obligation is {obligation.status.lower()} and cannot be paid")

    # ZR-PAY-LINK-003 Section 3.1: never let a payment session start against
    # a SUSPENDED connection (e.g. the recipient authority was just revoked)
    # just because their Stripe account happens to still be charge-ready --
    # crud/payment_connection.py:get_payment_connection_for_room is the one
    # place this is derived, never re-checked ad hoc here.
    room = obligation.room
    if room is not None:
        from app.crud.payment_connection import get_payment_connection_for_room

        connection = get_payment_connection_for_room(db, room)
        if connection.state == "SUSPENDED":
            raise HTTPException(status.HTTP_409_CONFLICT, "Payments for this room are currently suspended")

    # ZR-PAY-LINK-003 Section 22/AC-12: 'never show a method the backend
    # cannot lawfully or operationally execute' -- same
    # resolve_available_payment_methods gate
    # crud/payment_provider.py:renter_pay_obligation already enforces for
    # the older finance domain's own PSP-dispatched payments, applied here
    # too. Checked server-side, independently of whatever the connection
    # view showed the client -- never trusted as client input.
    from app.crud.market_policy import DEFAULT_JURISDICTION, resolve_available_payment_methods

    jurisdiction_code = obligation.jurisdiction_code or DEFAULT_JURISDICTION
    _require_market_approved_handoff(db, jurisdiction_code)
    available_methods = resolve_available_payment_methods(db, jurisdiction_code)
    if "CARD" not in available_methods:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Secure online payment is not available for this jurisdiction ({jurisdiction_code})",
        )

    from app.crud.rental_payment import assert_recipient_holds_payment_receipt_authority

    assert_recipient_holds_payment_receipt_authority(db, obligation, obligation.recipient_party_id)
    provider_account = get_charge_ready_provider_account_for_party(db, obligation.recipient_party_id)
    if provider_account is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The recipient has not connected a payment account yet")

    checkout_session_id, checkout_url = stripe_client.create_rent_payment_checkout_session(
        amount=float(obligation.amount), currency=obligation.currency,
        connected_account_id=provider_account.stripe_account_id,
        metadata={"domain": "rental_payment", "obligation_id": str(obligation.id), "tenant_guest_id": guest.id},
        success_url=success_url, cancel_url=cancel_url,
        product_name=f"{obligation.display_label.capitalize()} payment",
        idempotency_key=f"rental_payment_session:{obligation.id}:{guest.id}:{datetime.now(timezone.utc).timestamp()}",
    )

    session = ExternalPaymentSession(
        obligation_id=obligation.id, tenant_guest_id=guest.id,
        recipient_stripe_account_id=provider_account.stripe_account_id,
        provider_checkout_session_id=checkout_session_id, status="STARTED",
        amount=_round2(obligation.amount), currency=obligation.currency,
    )
    db.add(session)
    db.flush()

    from app.crud.rental_payment import recompute_obligation_status

    recompute_obligation_status(db, obligation)
    db.commit()
    db.refresh(session)

    emit_event(
        db, "rental_payment.session_started", "rental_payment_obligation", str(obligation.id),
        {"sessionId": session.id, "amount": float(session.amount), "currency": session.currency},
        correlation_id=correlation_id, actor_kind="guest", actor_id=guest.id,
    )
    db.commit()

    # Same "no real webhook will ever arrive without real Stripe keys
    # configured" honesty as crud/listing_fee.py:create_checkout's own
    # closing lines -- complete synchronously rather than leaving this
    # session stuck STARTED forever in dev/test. record_provider_payment_success
    # recomputes the obligation status again itself (STARTED -> CONFIRMED),
    # so the PAYMENT_SESSION_STARTED set just above is only ever
    # momentarily true in this fallback path -- harmless, same
    # recompute-after-every-state-relevant-mutation discipline as the rest
    # of this file.
    if not stripe_client.is_configured():
        record_provider_payment_success(
            db, session, payment_intent_id=checkout_session_id, correlation_id=correlation_id,
        )
        db.refresh(session)

    return session, checkout_url


def resolve_session(db: Session, session: ExternalPaymentSession) -> ExternalPaymentSession:
    """The return-page self-heal path -- same role
    stripe_client.retrieve_checkout_session plays for the Listing Fee return
    page: ask Stripe directly rather than only waiting on the webhook."""
    if session.status != "STARTED":
        return session
    result = stripe_client.retrieve_rent_payment_checkout_session(
        checkout_session_id=session.provider_checkout_session_id,
        connected_account_id=session.recipient_stripe_account_id,
    )
    if result is None:
        return session
    if result["payment_status"] == "paid":
        record_provider_payment_success(db, session, payment_intent_id=result["payment_intent_id"] or "")
        db.refresh(session)
    return session


def record_provider_payment_success(
    db: Session, session: ExternalPaymentSession, *, payment_intent_id: str, correlation_id: str = "",
) -> RentalPaymentRecord | None:
    """Idempotent on session.status -- a second call (webhook after
    resolve_session already self-healed, or a replayed webhook past the
    provider-event dedup layer) is a no-op, never a second
    RentalPaymentRecord."""
    if session.status != "STARTED":
        return None

    from app.crud.rental_payment import recompute_obligation_status

    session.status = "SUCCEEDED"
    session.provider_payment_intent_id = payment_intent_id
    session.resolved_at = datetime.now(timezone.utc)

    obligation = session.obligation
    record = RentalPaymentRecord(
        obligation_id=obligation.id, status="CONFIRMED", provenance="PROVIDER_CONFIRMATION",
        declared_amount=_round2(session.amount), declared_currency=session.currency, declared_date=datetime.now(timezone.utc).date(),
        payment_method_category="CARD", declared_by_guest_id=session.tenant_guest_id,
        confirmed_amount=_round2(session.amount), confirmed_at=datetime.now(timezone.utc),
        provider_reference=payment_intent_id,
    )
    db.add(record)
    db.flush()
    recompute_obligation_status(db, obligation)
    db.commit()
    db.refresh(record)

    emit_event(
        db, "rental_payment.receipt_confirmed", "rental_payment_record", str(record.id),
        {"obligationId": obligation.id, "sessionId": session.id, "providerReference": payment_intent_id},
        correlation_id=correlation_id, new_state="CONFIRMED",
    )
    db.commit()

    notif_crud.notify_user_by_guest(
        db, obligation.tenant, title="Payment confirmed",
        message="Payment confirmed by an integrated payment provider.",
        notification_type="rental_payment.receipt_confirmed",
        related_entity_type="rental_payment_record", related_entity_id=str(record.id),
    )
    notif_crud.notify_user_by_party(
        db, obligation.recipient_party_id, title="Payment received",
        message="A tenant paid via secure online payment -- review the record in Payments.",
        notification_type="rental_payment.receipt_confirmed",
        related_entity_type="rental_payment_record", related_entity_id=str(record.id),
    )
    return record


def record_provider_payment_failure(db: Session, session: ExternalPaymentSession, *, reason: str) -> ExternalPaymentSession:
    """No RentalPaymentRecord is created -- nothing to confirm, matching
    crud/listing_fee.py:_complete_payment_failure's own 'failure never
    becomes a record' posture. Recomputes the obligation status so a
    PAYMENT_SESSION_STARTED obligation correctly reverts to
    UPCOMING/DUE/OVERDUE once the session that put it there has failed."""
    if session.status != "STARTED":
        return session

    from app.crud.rental_payment import recompute_obligation_status

    session.status = "FAILED"
    session.failure_message = reason
    session.resolved_at = datetime.now(timezone.utc)
    db.flush()
    recompute_obligation_status(db, session.obligation)
    db.commit()
    db.refresh(session)
    return session


def ingest_stripe_webhook_event(db: Session, event, *, correlation_id: str = "") -> None:
    """Maps a real, already signature-verified Stripe Event for this domain.
    Idempotent via RentalPaymentProviderEvent's unique provider_event_id --
    same SAVEPOINT idiom as crud/listing_fee.py:ingest_stripe_webhook_event.

    Stripe delivers a connected account's own events to the PLATFORM's
    webhook endpoint too (not just a dedicated Connect endpoint) -- `event`
    carries an `account` field identifying which connected account it's
    for in that case. Only an event whose account is a connected account
    THIS domain actually knows about (a RentalPaymentProviderAccount row)
    is ever processed -- anything else (a platform-level event with no
    `account` at all, or one for some other connected account) is ignored.
    This is a real Stripe delivery behavior, not just a style choice."""
    event_type = STRIPE_EVENT_TYPE_MAP.get(event["type"])
    if event_type is None:
        return

    connected_account_id = event.get("account")
    if not connected_account_id:
        return
    known_account = db.scalar(
        select(RentalPaymentProviderAccount).where(RentalPaymentProviderAccount.stripe_account_id == connected_account_id)
    )
    if known_account is None:
        return

    provider_event_id = event["id"]
    stripe_object = event["data"]["object"]

    try:
        with db.begin_nested():
            db.add(RentalPaymentProviderEvent(
                provider_event_id=provider_event_id, event_type=event_type,
                processed_at=datetime.now(timezone.utc),
            ))
            db.flush()
    except IntegrityError:
        return  # already-seen event id -- idempotent no-op

    session = db.scalar(
        select(ExternalPaymentSession).where(ExternalPaymentSession.provider_checkout_session_id == stripe_object["id"])
    )
    if not session:
        return

    if event_type == "CHECKOUT_SESSION_ASYNC_PAYMENT_FAILED":
        record_provider_payment_failure(db, session, reason="Your payment method could not be charged.")
        return

    # CHECKOUT_SESSION_COMPLETED fires for both a synchronous method (card --
    # payment_status is already "paid") and the *start* of an async one
    # (payment_status "unpaid", resolved later by
    # checkout.session.async_payment_succeeded -- not yet mapped above,
    # matching this workstream's card-first scope) -- only "paid" is success.
    if stripe_object.get("payment_status") == "paid":
        payment_intent_id = stripe_object.get("payment_intent") or ""
        record_provider_payment_success(db, session, payment_intent_id=payment_intent_id, correlation_id=correlation_id)
