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
from sqlalchemy import func, select
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
    # A tenant's card chargeback against a rent payment. It's the host's own
    # dispute (their account carries the loss and answers it in their Stripe
    # Dashboard); it's mirrored here so neither side keeps treating the rent
    # as settled while the bank has the money in question.
    "charge.dispute.created": "CHARGE_DISPUTE_CREATED",
    "charge.dispute.updated": "CHARGE_DISPUTE_UPDATED",
    "charge.dispute.closed": "CHARGE_DISPUTE_CLOSED",
    # A refund on a rent payment -- issued by us (booking cancelled) or by
    # the host straight from their own Stripe Dashboard, which we'd
    # otherwise never hear about. charge.refund.updated also covers a refund
    # that later fails, which puts the money back with the host.
    "charge.refunded": "CHARGE_REFUNDED",
    "charge.refund.updated": "CHARGE_REFUND_UPDATED",
}


class ProviderEventNotReady(Exception):
    """A refund/dispute event for a rent payment we know about, arriving
    before that payment itself could be recorded. The webhook route answers
    it with a non-2xx so Stripe redelivers it later, instead of the event
    being acknowledged and lost."""


# Only these obligation statuses may open a new online payment. Everything
# else either is already paid/being paid (CONFIRMED, PAYER_RECORDED,
# RECIPIENT_CONFIRMATION_PENDING, PROVIDER_PROCESSING) or is under dispute.
# REVERSED is payable again: the earlier money went back. PARTIALLY_PAID is
# payable for the remainder only (RentalPaymentObligation.outstanding_amount)
# -- a checkout never charges more than is still owed.
PAYABLE_OBLIGATION_STATUSES = ("UPCOMING", "DUE", "OVERDUE", "REVERSED", "PARTIALLY_PAID")
OBLIGATION_NOT_PAYABLE_MESSAGES = {
    "CONFIRMED": "This obligation is already paid",
    "PAYER_RECORDED": "You've already recorded a payment for this obligation -- wait for the recipient to confirm it",
    "RECIPIENT_CONFIRMATION_PENDING": (
        "You've already recorded a payment for this obligation -- wait for the recipient to confirm it"
    ),
    "PROVIDER_PROCESSING": "A payment for this obligation is already being processed",
    "DISPUTED": "This obligation is under dispute and can't be paid until the dispute is resolved",
}


def _round2(amount) -> float:
    return round(float(amount), 2)


def _session_attempt_number(db: Session, obligation: RentalPaymentObligation) -> int:
    return db.scalar(
        select(func.count()).select_from(ExternalPaymentSession).where(ExternalPaymentSession.obligation_id == obligation.id)
    ) or 0


def _reuse_open_session(
    db: Session, obligation: RentalPaymentObligation, *, correlation_id: str = "",
) -> tuple[ExternalPaymentSession, str] | None:
    """A STARTED session already exists for this obligation. Ask Stripe what
    became of it rather than opening a second checkout alongside it:
    - still open -> hand back that same checkout page (a double-click or a
      second tab lands on the one payment already in progress);
    - actually paid (webhook not here yet) -> record it and refuse, it's paid;
    - expired/abandoned -> close it out as FAILED and return None, so the
      caller starts a fresh attempt.
    Without real Stripe keys there's nothing to ask, and the simulated path
    completes sessions synchronously anyway, so one left STARTED is refused
    as in progress rather than guessed at."""
    session = get_latest_session_for_obligation(db, obligation.id)
    if session is None or session.status != "STARTED":
        return None

    result = stripe_client.retrieve_rent_payment_checkout_session(
        checkout_session_id=session.provider_checkout_session_id,
        connected_account_id=session.recipient_stripe_account_id,
    )
    if result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A payment for this obligation is already in progress")

    if result["payment_status"] == "paid":
        record_provider_payment_success(
            db, session, payment_intent_id=result["payment_intent_id"] or "", correlation_id=correlation_id,
        )
        raise HTTPException(status.HTTP_409_CONFLICT, "This obligation is already paid")

    if result.get("status") == "open" and result.get("url") and _round2(session.amount) == obligation.outstanding_amount:
        return session, result["url"]

    # Expired, or the obligation was amended since this checkout opened --
    # either way this checkout must not be paid; the next attempt replaces it.
    # Expire it at Stripe too: once it's FAILED here, a late payment on it
    # would be ignored by record_provider_payment_success, i.e. money taken
    # but never recorded.
    if result.get("status") == "open":
        try:
            stripe_client.expire_rent_payment_checkout_session(
                checkout_session_id=session.provider_checkout_session_id,
                connected_account_id=session.recipient_stripe_account_id,
            )
        except Exception:
            # Stripe refuses to expire a session that completed in the
            # meantime -- the tenant just paid it. Record that, never drop it.
            latest = stripe_client.retrieve_rent_payment_checkout_session(
                checkout_session_id=session.provider_checkout_session_id,
                connected_account_id=session.recipient_stripe_account_id,
            )
            if latest is not None and latest["payment_status"] == "paid":
                record_provider_payment_success(
                    db, session, payment_intent_id=latest["payment_intent_id"] or "", correlation_id=correlation_id,
                )
                raise HTTPException(status.HTTP_409_CONFLICT, "This obligation is already paid")
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Couldn't close your previous checkout -- please try again",
            )
    record_provider_payment_failure(db, session, reason="This checkout expired before it was paid.")
    return None


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

    # Row-lock the obligation for the rest of this call, so two concurrent
    # requests (double-click, two tabs) serialize here: the second one waits,
    # then sees the first one's STARTED session below and reuses it rather
    # than opening a second, separately payable Stripe checkout.
    db.refresh(obligation, with_for_update=True)

    if obligation.status in ("WAIVED", "CANCELLED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This obligation is {obligation.status.lower()} and cannot be paid")

    reused = _reuse_open_session(db, obligation, correlation_id=correlation_id)
    if reused is not None:
        db.commit()  # nothing changed -- just releases the row lock
        return reused
    # _reuse_open_session may have committed (closing out a dead session),
    # which released the lock -- take it again before deciding anything.
    db.refresh(obligation, with_for_update=True)

    if obligation.status not in PAYABLE_OBLIGATION_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, OBLIGATION_NOT_PAYABLE_MESSAGES.get(
            obligation.status, f"This obligation cannot be paid online right now (status: {obligation.status})",
        ))
    if obligation.status == "PARTIALLY_PAID" and obligation.payer_allocations:
        # A joint tenancy's shares are settled per payer, not as one remainder.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Part of this shared obligation is already paid -- settle the remainder with the recipient directly",
        )
    db.expire(obligation, ["records"])  # outstanding_amount must see every record, not a stale list
    amount_due = obligation.outstanding_amount
    if amount_due <= 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "This obligation is already paid")

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
        amount=amount_due, currency=obligation.currency,
        connected_account_id=provider_account.stripe_account_id,
        metadata={"domain": "rental_payment", "obligation_id": str(obligation.id), "tenant_guest_id": guest.id},
        success_url=success_url, cancel_url=cancel_url,
        product_name=f"{obligation.display_label.capitalize()} payment",
        # Deterministic per attempt (not per wall-clock instant): a retry of
        # this same request -- e.g. Stripe created the session but our commit
        # below failed -- gets Stripe's already-created session back instead
        # of a second one. The attempt number moves on only once the previous
        # session has actually ended (FAILED/expired), and the amount is part
        # of the key so an amended (or partly paid) obligation never collides
        # with an old one.
        idempotency_key=(
            f"rental_payment_session:{obligation.id}:{guest.id}:"
            f"{_session_attempt_number(db, obligation)}:{amount_due}"
        ),
    )

    session = ExternalPaymentSession(
        obligation_id=obligation.id, tenant_guest_id=guest.id,
        recipient_stripe_account_id=provider_account.stripe_account_id,
        provider_checkout_session_id=checkout_session_id, status="STARTED",
        amount=amount_due, currency=obligation.currency,
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


def close_open_sessions_for_obligation(db: Session, obligation: RentalPaymentObligation) -> None:
    """Called when the obligation is going away (booking cancelled). Every
    STARTED checkout on it is settled against what Stripe actually says:
    - paid (webhook not here yet) -> recorded as a real payment, so the
      caller's refund step sends it back like any other;
    - still open -> expired at Stripe, so it can't be paid afterwards;
    - expired/abandoned -> closed out here.
    Never leaves a checkout that Stripe could still take money on."""
    sessions = db.scalars(
        select(ExternalPaymentSession).where(
            ExternalPaymentSession.obligation_id == obligation.id, ExternalPaymentSession.status == "STARTED",
        )
    ).all()
    for session in sessions:
        result = stripe_client.retrieve_rent_payment_checkout_session(
            checkout_session_id=session.provider_checkout_session_id,
            connected_account_id=session.recipient_stripe_account_id,
        )
        if result is not None and result["payment_status"] == "paid":
            record_provider_payment_success(db, session, payment_intent_id=result["payment_intent_id"] or "")
            continue
        if result is not None and result.get("status") == "open":
            try:
                stripe_client.expire_rent_payment_checkout_session(
                    checkout_session_id=session.provider_checkout_session_id,
                    connected_account_id=session.recipient_stripe_account_id,
                )
            except Exception as exc:
                latest = stripe_client.retrieve_rent_payment_checkout_session(
                    checkout_session_id=session.provider_checkout_session_id,
                    connected_account_id=session.recipient_stripe_account_id,
                )
                if latest is not None and latest["payment_status"] == "paid":
                    record_provider_payment_success(db, session, payment_intent_id=latest["payment_intent_id"] or "")
                    continue
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    "Couldn't close an open card checkout for this booking -- please try again",
                ) from exc
        record_provider_payment_failure(db, session, reason="The booking was cancelled before this checkout was paid.")


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


_OPEN_DISPUTE_NOTICE = (
    "Your bank has opened a dispute on this card payment. It's shown as disputed until the bank decides.",
    "A tenant's bank has disputed a card rent payment. Respond to it in your Stripe Dashboard -- "
    "the payment is shown as disputed until it's resolved.",
)


def apply_provider_state(record: RentalPaymentRecord) -> None:
    """The one place a PROVIDER_CONFIRMATION record's status is derived from
    what Stripe reports about its charge -- refunds and chargebacks combined,
    so neither can overwrite the other (a won dispute on a half-refunded
    payment is still half-refunded):
    - open dispute -> DISPUTED (the bank is holding the money);
    - dispute lost -> REVERSED (the bank returned all of it to the tenant);
    - refunded in full -> REVERSED; in part -> PARTIALLY_PAID;
    - otherwise -> CONFIRMED.
    confirmed_amount is always what the host actually kept, which is what
    RentalPaymentObligation.outstanding_amount sums."""
    gross = _round2(record.declared_amount)
    refunded = min(_round2(record.refunded_amount or 0), gross)
    dispute_status = record.provider_dispute_status or ""
    if dispute_status and dispute_status not in stripe_client.FINAL_DISPUTE_STATUSES:
        record.status = "DISPUTED"
    elif dispute_status == "lost":
        record.status = "REVERSED"
    elif refunded >= gross:
        record.status = "REVERSED"
    elif refunded > 0:
        record.status = "PARTIALLY_PAID"
    else:
        record.status = "CONFIRMED"
    record.confirmed_amount = 0.0 if dispute_status == "lost" else round(gross - refunded, 2)


def _find_provider_record(
    db: Session, payment_intent_id: str, connected_account_id: str,
) -> RentalPaymentRecord | None:
    """The rent payment a refund/dispute event is about, or None when it
    isn't one of ours. If the payment hasn't been recorded yet (this event
    beat checkout.session.completed here), it is recorded now from Stripe's
    own session state; if even that isn't possible yet, ProviderEventNotReady
    makes Stripe redeliver rather than the event being dropped."""
    session = db.scalar(
        select(ExternalPaymentSession).where(
            ExternalPaymentSession.provider_payment_intent_id == payment_intent_id,
            ExternalPaymentSession.recipient_stripe_account_id == connected_account_id,
        )
    )
    if session is None:
        checkout_session_id = stripe_client.find_rent_checkout_session_id_for_payment_intent(
            payment_intent_id=payment_intent_id, connected_account_id=connected_account_id,
        )
        if checkout_session_id:
            session = db.scalar(
                select(ExternalPaymentSession).where(
                    ExternalPaymentSession.provider_checkout_session_id == checkout_session_id,
                    ExternalPaymentSession.recipient_stripe_account_id == connected_account_id,
                )
            )
        if session is None:
            return None  # not a rent payment made through this platform
        if session.status == "STARTED":
            resolve_session(db, session)

    record = db.scalar(
        select(RentalPaymentRecord).where(
            RentalPaymentRecord.obligation_id == session.obligation_id,
            RentalPaymentRecord.provenance == "PROVIDER_CONFIRMATION",
            RentalPaymentRecord.provider_reference == payment_intent_id,
        )
    )
    if record is None:
        raise ProviderEventNotReady(f"rent payment {payment_intent_id} is not recorded yet")
    return record


def _notify_both(
    db: Session, obligation: RentalPaymentObligation, record: RentalPaymentRecord, *,
    title: str, notification_type: str, tenant_message: str, host_message: str,
) -> None:
    notif_crud.notify_user_by_guest(
        db, obligation.tenant, title=title, message=tenant_message, notification_type=notification_type,
        related_entity_type="rental_payment_record", related_entity_id=str(record.id),
    )
    notif_crud.notify_user_by_party(
        db, obligation.recipient_party_id, title=title, message=host_message, notification_type=notification_type,
        related_entity_type="rental_payment_record", related_entity_id=str(record.id),
    )


def record_provider_dispute(db: Session, dispute, *, event_type: str, connected_account_id: str) -> RentalPaymentRecord | None:
    """Mirrors a Stripe chargeback onto the rent payment it's against. While
    it's open the payment shows DISPUTED (not settled); once Stripe closes
    it, 'lost' makes it REVERSED -- the bank returned the money to the
    tenant, so the rent is owed again -- and any other outcome restores it
    (apply_provider_state keeps any earlier refund counted).
    Zoiko Rooms never pays or answers the dispute itself: the host's own
    connected account carries it (stripe_client.create_connected_account).

    Stripe doesn't deliver events in order, so the dispute's status is read
    back from Stripe itself when configured, and a dispute already seen as
    closed is never reopened by a late-arriving earlier event."""
    payment_intent_id = dispute.get("payment_intent") or ""
    dispute_id = dispute.get("id") or ""
    if not payment_intent_id:
        return None
    record = _find_provider_record(db, payment_intent_id, connected_account_id)
    if record is None:
        return None

    dispute_status = str(dispute.get("status") or "")
    if dispute_id:
        current = stripe_client.retrieve_rent_dispute(dispute_id=dispute_id, connected_account_id=connected_account_id)
        if current is not None:
            dispute_status = str(current["status"] or "")
    if (
        record.provider_dispute_id == dispute_id
        and record.provider_dispute_status in stripe_client.FINAL_DISPUTE_STATUSES
        and dispute_status not in stripe_client.FINAL_DISPUTE_STATUSES
    ):
        return record  # stale event for a dispute that has already closed

    previous_status = record.status
    record.provider_dispute_id = dispute_id or record.provider_dispute_id
    record.provider_dispute_status = dispute_status[:30]
    apply_provider_state(record)
    obligation = record.obligation
    db.flush()

    from app.crud.rental_payment import recompute_obligation_status

    recompute_obligation_status(db, obligation)
    db.commit()
    db.refresh(record)

    dispute_open = dispute_status not in stripe_client.FINAL_DISPUTE_STATUSES
    emit_event(
        db, "rental_payment.chargeback_" + ("opened" if dispute_open else "closed"),
        "rental_payment_record", str(record.id),
        {"obligationId": obligation.id, "disputeId": record.provider_dispute_id, "disputeStatus": dispute_status},
        new_state=record.status,
    )
    db.commit()

    if record.status == previous_status:
        return record  # status churn only -- both sides were told when it opened, and will be when it closes
    if dispute_open:
        tenant_message, host_message = _OPEN_DISPUTE_NOTICE
    elif dispute_status == "lost":
        tenant_message = "The dispute on this card payment was decided in your favour and the money returned -- this rent is due again."
        host_message = "A disputed card rent payment was returned to the tenant by their bank -- this rent is due again."
    elif dispute_status == "charge_refunded":
        tenant_message = "The dispute on this card payment was closed because the payment was refunded to you."
        host_message = "A disputed card rent payment was closed because it was refunded to the tenant."
    else:
        tenant_message = "The dispute on this card payment was closed and the payment stands."
        host_message = "A disputed card rent payment was decided in your favour -- the payment stands."
    _notify_both(
        db, obligation, record, title="Card payment dispute update", notification_type="rental_payment.chargeback",
        tenant_message=tenant_message, host_message=host_message,
    )
    return record


def record_provider_refund(db: Session, stripe_object, *, connected_account_id: str) -> RentalPaymentRecord | None:
    """Mirrors a refund on a rent card payment -- whether we issued it
    (crud/occupancy.py:cancel_before_move_in) or the host did from their own
    Stripe Dashboard. The refunded total is read back from Stripe when
    configured, so out-of-order delivery and failed refunds (Stripe lowers
    amount_refunded again) always settle on Stripe's current figure.
    stripe_object is a Charge (charge.refunded) or a Refund
    (charge.refund.updated) -- both carry payment_intent."""
    payment_intent_id = stripe_object.get("payment_intent") or ""
    if not payment_intent_id:
        return None
    record = _find_provider_record(db, payment_intent_id, connected_account_id)
    if record is None:
        return None

    state = stripe_client.retrieve_rent_charge_refund_state(
        payment_intent_id=payment_intent_id, connected_account_id=connected_account_id,
    )
    if state is not None:
        refunded, refund_id = state["amount_refunded"], state["latest_refund_id"]
    elif stripe_object.get("object") == "charge":
        refunded = stripe_client.from_minor_units(int(stripe_object.get("amount_refunded") or 0), record.declared_currency)
        refund_id = ""
    else:
        return record  # a bare Refund object carries no running total to trust

    previous_refunded = _round2(record.refunded_amount or 0)
    refunded = _round2(refunded)
    if refunded == previous_refunded and (not refund_id or refund_id == record.provider_refund_id):
        return record

    previous_status = record.status
    record.refunded_amount = refunded
    if refund_id:
        record.provider_refund_id = refund_id
    apply_provider_state(record)
    obligation = record.obligation
    db.flush()

    from app.crud.rental_payment import recompute_obligation_status

    recompute_obligation_status(db, obligation)
    db.commit()
    db.refresh(record)

    refund_failed = refunded < previous_refunded
    emit_event(
        db, "rental_payment.card_refund_" + ("failed" if refund_failed else "recorded"),
        "rental_payment_record", str(record.id),
        {"obligationId": obligation.id, "refundedAmount": refunded, "previousRefundedAmount": previous_refunded,
         "providerRefundId": record.provider_refund_id},
        new_state=record.status,
    )
    db.commit()

    if refund_failed:
        _notify_both(
            db, obligation, record, title="Card refund failed", notification_type="rental_payment.card_refund",
            tenant_message="A refund on your card payment didn't go through. Contact the host or support.",
            host_message="A refund on a card rent payment failed -- the money is back in your Stripe account.",
        )
    elif refunded > previous_refunded or record.status != previous_status:
        _notify_both(
            db, obligation, record, title="Card payment refunded", notification_type="rental_payment.card_refund",
            tenant_message=f"{record.declared_currency} {refunded:.2f} of your card payment has been refunded to you.",
            host_message=f"{record.declared_currency} {refunded:.2f} of a card rent payment was refunded to the tenant.",
        )
    return record


def ingest_stripe_webhook_event(db: Session, event, *, correlation_id: str = "") -> None:
    """Maps a real, already signature-verified Stripe Event for this domain.
    Idempotent via RentalPaymentProviderEvent's unique provider_event_id --
    same SAVEPOINT idiom as crud/listing_fee.py:ingest_stripe_webhook_event.
    The dedup row is committed only together with the event's own effects,
    so an event that raises ProviderEventNotReady is left unrecorded,
    redelivered by Stripe, and processed then.

    Events for a host's connected account carry an `account` field. Stripe
    only sends them to an endpoint registered as a *Connect* endpoint
    ("Listen to events on connected accounts") -- an ordinary account
    endpoint never receives them; backend/check_stripe_webhooks.py checks
    this. Only an event whose account is a connected account THIS domain
    knows about (a RentalPaymentProviderAccount row) is processed; anything
    else (a platform-level event with no `account`, or one for some other
    connected account) is ignored."""
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

    if event_type.startswith("CHARGE_DISPUTE_"):
        record_provider_dispute(db, stripe_object, event_type=event_type, connected_account_id=connected_account_id)
    elif event_type in ("CHARGE_REFUNDED", "CHARGE_REFUND_UPDATED"):
        record_provider_refund(db, stripe_object, connected_account_id=connected_account_id)
    else:
        session = db.scalar(
            select(ExternalPaymentSession).where(ExternalPaymentSession.provider_checkout_session_id == stripe_object["id"])
        )
        if session is None:
            pass
        elif event_type == "CHECKOUT_SESSION_ASYNC_PAYMENT_FAILED":
            record_provider_payment_failure(db, session, reason="Your payment method could not be charged.")
        # CHECKOUT_SESSION_COMPLETED fires for both a synchronous method (card --
        # payment_status is already "paid") and the *start* of an async one
        # (payment_status "unpaid", resolved later by
        # checkout.session.async_payment_succeeded -- not yet mapped above,
        # matching this workstream's card-first scope) -- only "paid" is success.
        elif stripe_object.get("payment_status") == "paid":
            payment_intent_id = stripe_object.get("payment_intent") or ""
            record_provider_payment_success(db, session, payment_intent_id=payment_intent_id, correlation_id=correlation_id)
    db.commit()  # the dedup row, even when the event itself needed no change
