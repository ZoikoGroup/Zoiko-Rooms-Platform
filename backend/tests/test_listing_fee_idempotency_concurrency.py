"""ZR-PAY-002 Section 12.2/13: 'Every write command that may be retried
must support idempotency' / 'Idempotency: Required for fee-payment and
refund commands.' ZR-WIR-TRACE-001 G9 (Reliability): 'Idempotency, retry,
concurrency ... tested.'

Simulates a concurrent retry landing in the database while the original
request is still mid-network-call to Stripe -- the realistic race window
for a payment API (the Stripe round-trip is the highest-latency step, so
it's where a client-side timeout-and-retry is most likely to overlap with
the original request's own DB write). Same 'two Sessions sharing one
connection' technique as test_dispute_hold_concurrency.py, but the
competing write happens inside a monkeypatched provider call rather than
between two full request executions, since create_checkout/request_refund
only reach their own DB insert *after* the (now idempotency-keyed) Stripe
call returns."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session as SASession

from app.crud import listing_fee as lf_crud
from app.models.listing_fee import ListingFeePayment, ListingFeeRefund
from app.schemas.listing_fee import ListingFeeRefundCreate
from tests.conftest import _make_admin
from tests.test_rental_payment_records import _make_party
from tests.test_zr_pay_002_acceptance_gates import _make_listing, _make_listing_fee_policy


class TestCreateCheckoutConcurrentRetry:
    def test_a_competing_insert_during_the_stripe_call_is_absorbed_not_a_500(self, db_session, monkeypatch):
        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-RACE-CHECKOUT", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)
        db_session.commit()

        idempotency_key = "race-checkout-key-1"
        winner_holder: dict[str, ListingFeePayment] = {}

        def _fake_create_payment_intent_with_client_secret(*, amount, currency, metadata, idempotency_key=None):
            # Simulate a second request (e.g. a client-side retry after a
            # timeout) reaching the database and committing first, while
            # this call is still "talking to Stripe". `idempotency_key`
            # here is the *namespaced Stripe-level* key create_checkout
            # passes in -- the DB row's own key is the outer `idempotency_key`.
            other_session = SASession(bind=db_session.bind)
            winner = ListingFeePayment(
                quote_id=quote.id, listing_id=quote.listing_id, party_id=party.id,
                amount=quote.total_amount, currency=quote.currency, idempotency_key="race-checkout-key-1",
                billing_country="GB", provider_payment_intent_id="pi_race_winner", status="SUCCEEDED",
            )
            other_session.add(winner)
            other_session.commit()
            winner_holder["winner_id"] = winner.id
            other_session.close()
            return "pi_race_loser", "secret_should_never_be_used"

        monkeypatch.setattr(
            lf_crud.stripe_client, "create_payment_intent_with_client_secret",
            _fake_create_payment_intent_with_client_secret,
        )

        payment, client_secret = lf_crud.create_checkout(
            db_session, quote, party, idempotency_key=idempotency_key, billing_country="GB",
        )

        # The DB-level SAVEPOINT backstop must return the winner's own row
        # (the one that actually reached the database first), never raise
        # an unhandled IntegrityError, and never silently create a second
        # payment row for the same idempotency key.
        assert payment.id == winner_holder["winner_id"]
        assert payment.provider_payment_intent_id == "pi_race_winner"
        assert client_secret == ""

        all_payments = db_session.query(ListingFeePayment).filter(
            ListingFeePayment.idempotency_key == idempotency_key
        ).all()
        assert len(all_payments) == 1


class TestRequestRefundConcurrentRetry:
    def test_a_competing_insert_during_the_refund_request_is_absorbed_not_a_500(self, db_session):
        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-RACE-REFUND", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)
        payment, _ = lf_crud.create_checkout(db_session, quote, party, idempotency_key="race-refund-setup", billing_country="GB")
        assert payment.status == "SUCCEEDED"
        db_session.commit()

        admin = _make_admin(db_session, email="race-refund-admin@test.com", role="super_admin")

        idempotency_key = "race-refund-key-1"
        # A second session commits the "winning" refund request row first,
        # simulating a retry that reached the database before this one's
        # own SAVEPOINT insert.
        other_session = SASession(bind=db_session.bind)
        winner = ListingFeeRefund(
            payment_id=payment.id, amount=float(payment.amount), currency=payment.currency, reason="race winner",
            idempotency_key=idempotency_key, requested_by_admin_id=admin.id, status="REQUESTED",
        )
        other_session.add(winner)
        other_session.commit()
        winner_id = winner.id
        other_session.close()

        refund = lf_crud.request_refund(
            db_session, admin, payment,
            ListingFeeRefundCreate(amount=float(payment.amount), reason="race loser", idempotency_key=idempotency_key),
        )

        assert refund.id == winner_id
        assert refund.reason == "race winner"

        all_refunds = db_session.query(ListingFeeRefund).filter(ListingFeeRefund.idempotency_key == idempotency_key).all()
        assert len(all_refunds) == 1


class TestStripeReceivesOwnIdempotencyKey:
    """A network-level retry must not create two real Stripe PaymentIntents
    even when both requests reach Stripe (not just our own database) --
    ZR-PAY-002 Section 13's idempotency requirement applies to the actual
    provider call, not only this app's own DB constraint."""

    def test_checkout_passes_a_namespaced_idempotency_key_to_stripe(self, db_session, monkeypatch):
        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-STRIPE-IDEM", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)

        captured = {}

        def _capture(*, amount, currency, metadata, idempotency_key=None):
            captured["idempotency_key"] = idempotency_key
            return "pi_captured", "secret"

        monkeypatch.setattr(lf_crud.stripe_client, "create_payment_intent_with_client_secret", _capture)

        lf_crud.create_checkout(db_session, quote, party, idempotency_key="my-key-123", billing_country="GB")
        assert captured["idempotency_key"] == "listing_fee_checkout:my-key-123"

    def test_refund_passes_a_namespaced_idempotency_key_to_stripe(self, db_session, monkeypatch):
        party = _make_party(db_session, party_type="provider")
        listing = _make_listing(db_session, listing_id="L-STRIPE-IDEM-REFUND", party_id=party.id)
        _make_listing_fee_policy(db_session)
        quote = lf_crud.create_quote(db_session, listing, party)
        payment, _ = lf_crud.create_checkout(db_session, quote, party, idempotency_key="refund-idem-setup", billing_country="GB")
        assert payment.status == "SUCCEEDED"
        admin = _make_admin(db_session, email="stripe-idem-refund-admin@test.com", role="super_admin")

        captured = {}

        def _capture(*, payment_intent_id, amount, currency, metadata, idempotency_key=None):
            captured["idempotency_key"] = idempotency_key
            return "re_captured"

        monkeypatch.setattr(lf_crud.stripe_client, "create_refund", _capture)

        lf_crud.request_refund(
            db_session, admin, payment,
            ListingFeeRefundCreate(amount=float(payment.amount), reason="test", idempotency_key="my-refund-key-456"),
        )
        assert captured["idempotency_key"] == "listing_fee_refund:my-refund-key-456"
