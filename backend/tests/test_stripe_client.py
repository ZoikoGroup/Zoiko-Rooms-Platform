"""Regression coverage for app/services/stripe_client.py's real (configured)
Stripe path. The test suite otherwise forces stripe_secret_key blank (see
conftest.py:_isolate_from_real_provider_credentials), so every other test
only ever exercises the simulated fallback -- this file is the one place
that turns "configured" back on, under a monkeypatched stripe.checkout.Session
so no real network call happens."""

import stripe

from app.services import stripe_client


class TestCreateCheckoutSession:
    def test_creates_a_real_hosted_checkout_session(self, monkeypatch):
        """The Listing Fee is a real, separate Stripe-hosted payment page --
        the frontend does a real browser redirect to checkout_url, it never
        renders its own card form. Returns the Checkout Session's own id,
        not a PaymentIntent id -- confirmed against the real API that
        session.payment_intent is still None immediately after creation
        (Stripe only creates one once the customer actually pays), so that
        id is never available to return here. Locking in this shape so it
        can't silently regress back to the old embedded-Elements
        PaymentIntent approach, or to assuming payment_intent is
        available too early."""
        monkeypatch.setattr(stripe_client.settings, "stripe_secret_key", "sk_test_fake")

        captured = {}

        class _FakeSession:
            id = "cs_fake123"
            payment_intent = None
            url = "https://checkout.stripe.com/c/pay/cs_fake123"

        def _fake_create(**kwargs):
            captured.update(kwargs)
            return _FakeSession()

        monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(_fake_create))

        checkout_session_id, checkout_url = stripe_client.create_checkout_session(
            amount=30.0, currency="GBP", metadata={"domain": "listing_fee"},
            success_url="https://app.test/success", cancel_url="https://app.test/cancel",
        )

        assert checkout_session_id == "cs_fake123"
        assert checkout_url == "https://checkout.stripe.com/c/pay/cs_fake123"
        assert captured["mode"] == "payment"
        assert captured["success_url"] == "https://app.test/success"
        assert captured["cancel_url"] == "https://app.test/cancel"
        assert captured["line_items"][0]["price_data"]["unit_amount"] == 3000

    def test_falls_back_to_simulated_id_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(stripe_client.settings, "stripe_secret_key", "")

        checkout_session_id, checkout_url = stripe_client.create_checkout_session(
            amount=30.0, currency="GBP", metadata={},
            success_url="https://app.test/success", cancel_url="https://app.test/cancel",
        )

        assert checkout_session_id.startswith("PAYTXN")
        assert checkout_url == ""
