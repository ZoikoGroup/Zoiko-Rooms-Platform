"""Coverage for the Stripe-hosted Checkout Session flow's own webhook
correlation -- distinct from the older raw-PaymentIntent webhook path
(test_zr_pay_002_acceptance_gates.py::TestA12WebhookIdempotency), because
Stripe does not create the underlying PaymentIntent until the customer
actually completes the hosted page (confirmed against the real API --
session.payment_intent is None immediately after Session.create()). Every
Checkout-Session-initiated payment is therefore first correlated by
provider_checkout_session_id (known synchronously at checkout creation),
never by provider_payment_intent_id (only known once one of the events
tested here backfills it)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.crud import listing_fee as lf_crud
from app.models.listing_fee import ListingFeePayment, ListingFeePolicy
from tests.test_rental_payment_records import _make_party
from tests.test_zr_pay_002_acceptance_gates import _make_listing


def _make_listing_fee_policy(db: Session) -> ListingFeePolicy:
    policy = ListingFeePolicy(
        jurisdiction_code="England", version=1, effective_from=date(2020, 1, 1),
        amount=25, currency="GBP", tax_rate=0.2, quote_validity_minutes=30,
        legal_entity_name="Zoiko Realty Group", tax_registration_number="GB123",
    )
    db.add(policy)
    db.flush()
    return policy


def _make_pending_checkout_session_payment(db: Session, *, checkout_session_id: str) -> tuple[ListingFeePayment, object]:
    party = _make_party(db, party_type="provider")
    listing = _make_listing(db, listing_id=f"L-CS-{checkout_session_id}", party_id=party.id)
    _make_listing_fee_policy(db)
    quote = lf_crud.create_quote(db, listing, party)
    payment = ListingFeePayment(
        quote_id=quote.id, listing_id=listing.id, party_id=party.id, amount=float(quote.total_amount),
        currency=quote.currency, idempotency_key=f"idem-{checkout_session_id}", billing_country="GB",
        provider_checkout_session_id=checkout_session_id,
    )
    db.add(payment)
    db.commit()
    return payment, listing


class TestCheckoutSessionCompleted:
    def test_paid_session_backfills_payment_intent_and_completes_payment(self, db_session):
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_paid_1")
        assert payment.status == "PENDING"
        assert payment.provider_payment_intent_id is None

        event = {
            "id": "evt_cs_paid_1", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_paid_1", "payment_status": "paid", "payment_intent": "pi_from_cs_paid_1"}},
        }
        lf_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(payment)

        assert payment.status == "SUCCEEDED"
        assert payment.provider_payment_intent_id == "pi_from_cs_paid_1"

    def test_unpaid_session_completed_does_not_mark_success(self, db_session):
        """An async payment method's session.completed fires with
        payment_status still 'unpaid' -- the later
        checkout.session.async_payment_succeeded event (mapped to the same
        internal type) is what actually completes it."""
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_unpaid_1")

        event = {
            "id": "evt_cs_unpaid_1", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_unpaid_1", "payment_status": "unpaid", "payment_intent": "pi_from_cs_unpaid_1"}},
        }
        lf_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(payment)

        assert payment.status == "PENDING"

    def test_async_payment_succeeded_completes_a_previously_unpaid_session(self, db_session):
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_async_ok_1")

        lf_crud.ingest_stripe_webhook_event(db_session, {
            "id": "evt_cs_async_1", "type": "checkout.session.async_payment_succeeded",
            "data": {"object": {"id": "cs_async_ok_1", "payment_status": "paid", "payment_intent": "pi_async_1"}},
        })
        db_session.refresh(payment)

        assert payment.status == "SUCCEEDED"
        assert payment.provider_payment_intent_id == "pi_async_1"

    def test_replayed_event_id_is_a_no_op(self, db_session):
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_replay_1")
        event = {
            "id": "evt_cs_replay_1", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_replay_1", "payment_status": "paid", "payment_intent": "pi_replay_1"}},
        }
        lf_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(payment)
        first_paid_at = payment.paid_at

        lf_crud.ingest_stripe_webhook_event(db_session, event)
        db_session.refresh(payment)
        assert payment.paid_at == first_paid_at

    def test_unknown_checkout_session_id_is_ignored_not_an_error(self, db_session):
        # No payment row exists for this session id at all -- must not raise.
        lf_crud.ingest_stripe_webhook_event(db_session, {
            "id": "evt_cs_unknown_1", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_never_created", "payment_status": "paid", "payment_intent": "pi_never"}},
        })


class TestCheckoutSessionAsyncPaymentFailed:
    def test_marks_the_payment_failed(self, db_session):
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_failed_1")

        lf_crud.ingest_stripe_webhook_event(db_session, {
            "id": "evt_cs_failed_1", "type": "checkout.session.async_payment_failed",
            "data": {"object": {"id": "cs_failed_1"}},
        })
        db_session.refresh(payment)

        assert payment.status == "FAILED"


class TestFrontendOriginResolution:
    """A static settings.frontend_url can drift out of sync with whatever
    port/host the browser is actually being served from (a local Next.js
    dev server auto-increments its port when the default is taken -- the
    exact mismatch that once sent a real Stripe redirect to a dead port,
    ERR_CONNECTION_REFUSED). create_checkout's frontend_origin parameter
    exists so the redirect target is derived from the request that's
    actually making the call, not a static config value."""

    def test_create_checkout_uses_the_supplied_frontend_origin(self, db_session, monkeypatch):
        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-ORIGIN-1", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)

        captured = {}

        def _capture(*, amount, currency, metadata, success_url, cancel_url, idempotency_key=None):
            captured["success_url"] = success_url
            captured["cancel_url"] = cancel_url
            return "cs_origin_test", ""

        monkeypatch.setattr(lf_crud.stripe_client, "create_checkout_session", _capture)

        lf_crud.create_checkout(
            db_session, quote, party, idempotency_key="origin-test-1", billing_country="GB",
            frontend_origin="http://localhost:3001",
        )

        assert captured["success_url"].startswith("http://localhost:3001/account/host/listings?")
        assert captured["cancel_url"].startswith("http://localhost:3001/account/host/listings?")
        assert "{CHECKOUT_SESSION_ID}" in captured["success_url"]

    def test_falls_back_to_settings_frontend_url_when_no_origin_supplied(self, db_session, monkeypatch):
        from app.core.config import settings

        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-ORIGIN-2", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)

        captured = {}

        def _capture(*, amount, currency, metadata, success_url, cancel_url, idempotency_key=None):
            captured["success_url"] = success_url
            return "cs_origin_test_2", ""

        monkeypatch.setattr(lf_crud.stripe_client, "create_checkout_session", _capture)

        lf_crud.create_checkout(db_session, quote, party, idempotency_key="origin-test-2", billing_country="GB")

        assert captured["success_url"].startswith(settings.frontend_url)


class TestResolveFrontendOriginFromRequest:
    def test_uses_the_request_origin_when_it_is_an_allowed_cors_origin(self, monkeypatch):
        from unittest.mock import MagicMock

        from app.api.routes.listing_fees import _resolve_frontend_origin, settings

        monkeypatch.setattr(settings, "cors_origins", "http://localhost:3001,http://localhost:3000")

        request = MagicMock()
        request.headers.get.return_value = "http://localhost:3001"
        assert _resolve_frontend_origin(request) == "http://localhost:3001"

    def test_falls_back_when_origin_is_not_allowlisted(self):
        from unittest.mock import MagicMock

        from app.api.routes.listing_fees import _resolve_frontend_origin
        from app.core.config import settings

        request = MagicMock()
        request.headers.get.return_value = "https://attacker-controlled.example"
        assert _resolve_frontend_origin(request) == settings.frontend_url

    def test_falls_back_when_no_origin_header_present(self):
        from unittest.mock import MagicMock

        from app.api.routes.listing_fees import _resolve_frontend_origin
        from app.core.config import settings

        request = MagicMock()
        request.headers.get.return_value = None
        assert _resolve_frontend_origin(request) == settings.frontend_url


class TestGetPaymentByCheckoutSessionId:
    def test_resolves_the_payment_from_our_own_db_no_stripe_call(self, db_session):
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_resolve_1")

        resolved = lf_crud.get_payment_by_checkout_session_id(db_session, "cs_resolve_1")
        assert resolved.id == payment.id

    def test_unknown_session_id_is_a_404(self, db_session):
        from fastapi import HTTPException

        try:
            lf_crud.get_payment_by_checkout_session_id(db_session, "cs_does_not_exist")
            assert False, "expected HTTPException"
        except HTTPException as e:
            assert e.status_code == 404

    def test_self_heals_a_pending_payment_when_stripe_already_shows_it_paid(self, db_session, monkeypatch):
        """The customer's own return trip to success_url must not be left
        hostage to webhook delivery having already happened by the time
        they land there -- local dev without `stripe listen` running is the
        common case this protects against, but ordinary webhook latency in
        production is the same shape of problem."""
        payment, listing = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_heal_1")
        assert payment.status == "PENDING"

        monkeypatch.setattr(
            lf_crud.stripe_client, "retrieve_checkout_session",
            lambda *, checkout_session_id: {"payment_status": "paid", "payment_intent_id": "pi_healed_1"},
        )

        resolved = lf_crud.get_payment_by_checkout_session_id(db_session, "cs_heal_1")

        assert resolved.status == "SUCCEEDED"
        assert resolved.provider_payment_intent_id == "pi_healed_1"

    def test_does_not_call_stripe_again_once_already_succeeded(self, db_session, monkeypatch):
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_heal_2")
        payment.status = "SUCCEEDED"
        db_session.commit()

        def _should_not_be_called(*, checkout_session_id):
            raise AssertionError("retrieve_checkout_session must not be called for an already-resolved payment")

        monkeypatch.setattr(lf_crud.stripe_client, "retrieve_checkout_session", _should_not_be_called)

        resolved = lf_crud.get_payment_by_checkout_session_id(db_session, "cs_heal_2")
        assert resolved.status == "SUCCEEDED"

    def test_leaves_payment_pending_when_stripe_says_still_unpaid(self, db_session, monkeypatch):
        payment, _ = _make_pending_checkout_session_payment(db_session, checkout_session_id="cs_heal_3")

        monkeypatch.setattr(
            lf_crud.stripe_client, "retrieve_checkout_session",
            lambda *, checkout_session_id: {"payment_status": "unpaid", "payment_intent_id": None},
        )

        resolved = lf_crud.get_payment_by_checkout_session_id(db_session, "cs_heal_3")
        assert resolved.status == "PENDING"
