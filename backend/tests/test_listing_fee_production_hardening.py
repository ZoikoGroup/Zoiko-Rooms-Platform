"""Listing Fee production hardening: the charge.refunded webhook actually
finds its payment, partial refunds add up, abandoned checkouts expire, a
second checkout resumes the open session instead of opening another
payable one, and a fully refunded fee no longer satisfies the publish gate."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.crud import listing_fee as lf_crud
from app.models.listing_fee import ListingFeePayment, ListingFeeRefund
from tests.test_listing_fee_checkout_session_webhook import (
    _make_listing_fee_policy,
    _make_pending_checkout_session_payment,
)
from tests.conftest import _make_admin
from tests.test_rental_payment_records import _make_party
from tests.test_zr_pay_002_acceptance_gates import _make_listing


def _succeeded_payment(db, *, session_id: str, intent_id: str) -> ListingFeePayment:
    payment, _ = _make_pending_checkout_session_payment(db, checkout_session_id=session_id)
    payment.status = "SUCCEEDED"
    payment.paid_at = datetime.now(timezone.utc)
    payment.provider_payment_intent_id = intent_id
    db.commit()
    return payment


def _processing_refund(db, payment: ListingFeePayment, amount: float, key: str) -> ListingFeeRefund:
    admin = _make_admin(db, email=f"refund-{key}@test.com", role="super_admin")
    refund = ListingFeeRefund(
        payment_id=payment.id, amount=amount, currency=payment.currency, reason="test",
        idempotency_key=key, status="PROCESSING", provider_refund_id=f"re_{key}", requested_by_admin_id=admin.id,
    )
    db.add(refund)
    db.commit()
    return refund


def _charge_refunded_event(event_id: str, *, intent_id: str, amount_refunded_minor: int) -> dict:
    return {
        "id": event_id, "type": "charge.refunded",
        "data": {"object": {
            "id": f"ch_{event_id}", "object": "charge", "payment_intent": intent_id,
            "amount_refunded": amount_refunded_minor,
        }},
    }


class TestChargeRefundedWebhook:
    def test_a_charge_event_confirms_the_refund_via_its_payment_intent(self, db_session):
        payment = _succeeded_payment(db_session, session_id="cs_refund_full", intent_id="pi_refund_full")
        refund = _processing_refund(db_session, payment, float(payment.amount), "full")

        lf_crud.ingest_stripe_webhook_event(
            db_session,
            _charge_refunded_event("evt_full", intent_id="pi_refund_full",
                                   amount_refunded_minor=round(float(payment.amount) * 100)),
        )
        db_session.refresh(refund)

        assert refund.status == "REFUNDED"
        assert refund.completed_at is not None

    def test_two_partial_refunds_add_up_to_a_full_refund(self, db_session):
        payment = _succeeded_payment(db_session, session_id="cs_refund_parts", intent_id="pi_refund_parts")
        total = float(payment.amount)
        first = _processing_refund(db_session, payment, 10.0, "part1")

        lf_crud.ingest_stripe_webhook_event(
            db_session, _charge_refunded_event("evt_part1", intent_id="pi_refund_parts", amount_refunded_minor=1000),
        )
        db_session.refresh(first)
        assert first.status == "PARTIALLY_REFUNDED"

        second = _processing_refund(db_session, payment, round(total - 10.0, 2), "part2")
        lf_crud.ingest_stripe_webhook_event(
            db_session,
            _charge_refunded_event("evt_part2", intent_id="pi_refund_parts", amount_refunded_minor=round(total * 100)),
        )
        db_session.refresh(first)
        db_session.refresh(second)

        assert first.status == "PARTIALLY_REFUNDED"
        assert second.status == "REFUNDED"

    def test_a_refund_stripe_has_not_yet_reported_stays_processing(self, db_session):
        payment = _succeeded_payment(db_session, session_id="cs_refund_early", intent_id="pi_refund_early")
        first = _processing_refund(db_session, payment, 5.0, "early1")
        second = _processing_refund(db_session, payment, 5.0, "early2")

        lf_crud.ingest_stripe_webhook_event(
            db_session, _charge_refunded_event("evt_early", intent_id="pi_refund_early", amount_refunded_minor=500),
        )
        db_session.refresh(first)
        db_session.refresh(second)

        assert first.status == "PARTIALLY_REFUNDED"
        assert second.status == "PROCESSING"


class TestFullyRefundedFeeNoLongerCountsAsPaid:
    def test_full_refund_reopens_the_publish_gate_but_partial_does_not(self, db_session):
        payment = _succeeded_payment(db_session, session_id="cs_gate", intent_id="pi_gate")
        assert lf_crud.listing_fee_is_paid(db_session, payment.listing_id)

        partial = _processing_refund(db_session, payment, 5.0, "gate-partial")
        partial.status = "PARTIALLY_REFUNDED"
        db_session.commit()
        assert lf_crud.listing_fee_is_paid(db_session, payment.listing_id)

        full = _processing_refund(db_session, payment, round(float(payment.amount) - 5.0, 2), "gate-full")
        full.status = "REFUNDED"
        db_session.commit()
        assert not lf_crud.listing_fee_is_paid(db_session, payment.listing_id)


class TestCheckoutSessionExpired:
    def test_expired_event_fails_a_pending_payment(self, db_session):
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_expired_1")

        lf_crud.ingest_stripe_webhook_event(
            db_session,
            {"id": "evt_expired_1", "type": "checkout.session.expired",
             "data": {"object": {"id": "cs_expired_1", "payment_status": "unpaid", "status": "expired"}}},
        )
        db_session.refresh(payment)

        assert payment.status == "FAILED"
        assert payment.failure_message == lf_crud.CHECKOUT_EXPIRED_MESSAGE

    def test_expired_event_never_downgrades_a_succeeded_payment(self, db_session):
        payment = _succeeded_payment(db_session, session_id="cs_expired_paid", intent_id="pi_expired_paid")

        lf_crud.ingest_stripe_webhook_event(
            db_session,
            {"id": "evt_expired_paid", "type": "checkout.session.expired",
             "data": {"object": {"id": "cs_expired_paid", "status": "expired"}}},
        )
        db_session.refresh(payment)

        assert payment.status == "SUCCEEDED"


class TestSecondCheckoutResumesTheOpenSession:
    """A fresh idempotency key per click must not open a second payable
    Stripe session while the first is still open."""

    def _setup(self, db, monkeypatch, *, listing_id: str, session_state: dict):
        party = _make_party(db, party_type="provider")
        listing = _make_listing(db, listing_id=listing_id, party_id=party.id)
        _make_listing_fee_policy(db)
        quote = lf_crud.create_quote(db, listing, party)
        pending = ListingFeePayment(
            quote_id=quote.id, listing_id=listing.id, party_id=party.id, amount=float(quote.total_amount),
            currency=quote.currency, idempotency_key=f"first-{listing_id}", billing_country="GB",
            provider_checkout_session_id=f"cs_{listing_id}",
        )
        db.add(pending)
        db.commit()

        created: list[str] = []

        def _create(**kwargs):
            created.append(kwargs["idempotency_key"])
            return f"cs_new_{listing_id}", "https://checkout.stripe.test/new"

        monkeypatch.setattr(lf_crud.stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(lf_crud.stripe_client, "retrieve_checkout_session", lambda **kw: session_state)
        monkeypatch.setattr(lf_crud.stripe_client, "create_checkout_session", _create)
        return party, quote, pending, created

    def test_an_open_session_is_returned_instead_of_creating_another(self, db_session, monkeypatch):
        party, quote, pending, created = self._setup(
            db_session, monkeypatch, listing_id="L-RESUME-OPEN",
            session_state={"payment_status": "unpaid", "status": "open", "url": "https://checkout.stripe.test/first"},
        )

        payment, url = lf_crud.create_checkout(db_session, quote, party, idempotency_key="second-click", billing_country="GB")

        assert payment.id == pending.id
        assert url == "https://checkout.stripe.test/first"
        assert created == []

    def test_an_expired_session_is_failed_and_a_new_one_created(self, db_session, monkeypatch):
        party, quote, pending, created = self._setup(
            db_session, monkeypatch, listing_id="L-RESUME-EXPIRED",
            session_state={"payment_status": "unpaid", "status": "expired", "url": ""},
        )

        payment, url = lf_crud.create_checkout(db_session, quote, party, idempotency_key="after-expiry", billing_country="GB")
        db_session.refresh(pending)

        assert pending.status == "FAILED"
        assert payment.id != pending.id
        assert url == "https://checkout.stripe.test/new"
        assert created == ["listing_fee_checkout:after-expiry"]

    def test_an_already_paid_session_blocks_a_new_checkout(self, db_session, monkeypatch):
        party, quote, pending, created = self._setup(
            db_session, monkeypatch, listing_id="L-RESUME-PAID",
            session_state={"payment_status": "paid", "status": "complete", "payment_intent_id": "pi_resume_paid", "url": ""},
        )

        with pytest.raises(HTTPException) as exc:
            lf_crud.create_checkout(db_session, quote, party, idempotency_key="late-click", billing_country="GB")
        db_session.refresh(pending)

        assert exc.value.status_code == 409
        assert pending.status == "SUCCEEDED"
        assert created == []

    def test_an_async_payment_still_settling_blocks_a_new_checkout(self, db_session, monkeypatch):
        party, quote, pending, created = self._setup(
            db_session, monkeypatch, listing_id="L-RESUME-ASYNC",
            session_state={"payment_status": "unpaid", "status": "complete", "url": ""},
        )

        with pytest.raises(HTTPException) as exc:
            lf_crud.create_checkout(db_session, quote, party, idempotency_key="impatient-click", billing_country="GB")

        assert exc.value.status_code == 409
        assert "still being processed" in exc.value.detail
        assert created == []

    def test_a_retried_request_gets_the_live_checkout_url_back(self, db_session, monkeypatch):
        party, quote, pending, created = self._setup(
            db_session, monkeypatch, listing_id="L-RESUME-RETRY",
            session_state={"payment_status": "unpaid", "status": "open", "url": "https://checkout.stripe.test/first"},
        )

        payment, url = lf_crud.create_checkout(
            db_session, quote, party, idempotency_key=pending.idempotency_key, billing_country="GB",
        )

        assert payment.id == pending.id
        assert url == "https://checkout.stripe.test/first"


def _dispute_event(event_id: str, *, event_type: str, intent_id: str, status: str) -> dict:
    return {
        "id": event_id, "type": event_type,
        "data": {"object": {
            "id": f"dp_{intent_id}", "object": "dispute", "payment_intent": intent_id,
            "charge": f"ch_{intent_id}", "status": status, "reason": "fraudulent",
        }},
    }


class TestChargebacks:
    def test_open_dispute_blocks_the_gate_and_a_won_one_restores_it(self, db_session):
        payment = _succeeded_payment(db_session, session_id="cs_dispute_won", intent_id="pi_dispute_won")
        assert lf_crud.listing_fee_is_paid(db_session, payment.listing_id)

        lf_crud.ingest_stripe_webhook_event(db_session, _dispute_event(
            "evt_dp_open", event_type="charge.dispute.created", intent_id="pi_dispute_won", status="needs_response",
        ))
        db_session.refresh(payment)
        assert payment.dispute_status == "OPEN"
        assert payment.disputed_at is not None
        assert not lf_crud.listing_fee_is_paid(db_session, payment.listing_id)

        lf_crud.ingest_stripe_webhook_event(db_session, _dispute_event(
            "evt_dp_won", event_type="charge.dispute.closed", intent_id="pi_dispute_won", status="won",
        ))
        db_session.refresh(payment)
        assert payment.dispute_status == "WON"
        assert lf_crud.listing_fee_is_paid(db_session, payment.listing_id)

    def test_a_lost_dispute_keeps_the_gate_closed(self, db_session):
        payment = _succeeded_payment(db_session, session_id="cs_dispute_lost", intent_id="pi_dispute_lost")
        for event_id, event_type, status in (
            ("evt_dpl_open", "charge.dispute.created", "needs_response"),
            ("evt_dpl_lost", "charge.dispute.closed", "lost"),
        ):
            lf_crud.ingest_stripe_webhook_event(
                db_session, _dispute_event(event_id, event_type=event_type, intent_id="pi_dispute_lost", status=status),
            )
        db_session.refresh(payment)

        assert payment.dispute_status == "LOST"
        assert payment.status == "SUCCEEDED"
        assert not lf_crud.listing_fee_is_paid(db_session, payment.listing_id)


class TestRefundIssuedInStripeDashboard:
    def test_an_unrecorded_refund_is_recorded_and_confirmed(self, db_session, monkeypatch):
        _make_admin(db_session, email="system@test.com", role="super_admin")
        monkeypatch.setattr(lf_crud.settings, "seed_admin_email", "system@test.com")
        payment = _succeeded_payment(db_session, session_id="cs_dash_refund", intent_id="pi_dash_refund")

        lf_crud.ingest_stripe_webhook_event(
            db_session, _charge_refunded_event("evt_dash", intent_id="pi_dash_refund", amount_refunded_minor=700),
        )
        refunds = db_session.query(ListingFeeRefund).filter_by(payment_id=payment.id).all()

        assert len(refunds) == 1
        assert float(refunds[0].amount) == 7.0
        assert refunds[0].status == "PARTIALLY_REFUNDED"
        assert refunds[0].idempotency_key == "stripe_dashboard:evt_dash"

    def test_an_app_refund_is_not_recorded_twice(self, db_session, monkeypatch):
        _make_admin(db_session, email="system2@test.com", role="super_admin")
        monkeypatch.setattr(lf_crud.settings, "seed_admin_email", "system2@test.com")
        payment = _succeeded_payment(db_session, session_id="cs_app_refund", intent_id="pi_app_refund")
        _processing_refund(db_session, payment, 5.0, "app-only")

        lf_crud.ingest_stripe_webhook_event(
            db_session, _charge_refunded_event("evt_app", intent_id="pi_app_refund", amount_refunded_minor=500),
        )

        assert db_session.query(ListingFeeRefund).filter_by(payment_id=payment.id).count() == 1


class TestReceiptFailureStillNotifies:
    def test_payment_succeeds_and_notifies_even_if_the_receipt_cannot_be_saved(self, db_session, monkeypatch):
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_receipt_fail")
        notified: list[str] = []

        def _broken_receipt(db, payment):
            raise OSError("disk full")

        monkeypatch.setattr(lf_crud, "get_or_create_listing_fee_receipt", _broken_receipt)
        monkeypatch.setattr(
            lf_crud.notif_crud, "notify_user_by_party", lambda db, party_id, **kw: notified.append(kw["title"]),
        )

        lf_crud._complete_payment_success(db_session, payment)
        db_session.refresh(payment)

        assert payment.status == "SUCCEEDED"
        assert notified == ["Listing Fee paid"]


def test_hosted_checkout_expires_with_the_quote_not_after_24_hours(monkeypatch):
    from app.services import stripe_client

    captured: dict = {}

    class _FakeSession:
        id, url = "cs_ttl", "https://checkout.stripe.test/ttl"

    class _FakeStripe:
        class checkout:
            class Session:
                @staticmethod
                def create(**kwargs):
                    captured.update(kwargs)
                    return _FakeSession()

    monkeypatch.setattr(stripe_client.settings, "stripe_secret_key", "sk_test_ttl")
    monkeypatch.setattr(stripe_client, "_client", lambda: _FakeStripe)
    import time

    before = int(time.time())
    stripe_client.create_checkout_session(
        amount=20, currency="GBP", metadata={}, success_url="https://x/s", cancel_url="https://x/c",
    )

    ttl = captured["expires_at"] - before
    assert 30 * 60 < ttl <= 32 * 60
