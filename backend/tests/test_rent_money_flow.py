"""Rent is strictly between tenant and payee, paid directly by bank
transfer, UPI or cash -- there is no card / Stripe rail for rent. The only
Stripe Connect account left is the legacy (off-by-default) payout
recipient, whose shape is pinned here."""

from __future__ import annotations

from types import SimpleNamespace

from app.services import stripe_client


class TestRentAccountsCarryTheirOwnFeesAndLosses:
    def _capture_account_create(self, monkeypatch) -> dict:
        captured: dict = {}

        def create(*, params):
            captured.update(params)
            return SimpleNamespace(id="acct_test_created")

        fake = SimpleNamespace(v2=SimpleNamespace(core=SimpleNamespace(accounts=SimpleNamespace(create=create))))
        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "stripe_client_v2", lambda: fake)
        return captured

    def test_legacy_payout_recipient_account_is_unchanged(self, monkeypatch):
        captured = self._capture_account_create(monkeypatch)
        stripe_client.create_connected_account(country="GB", email="host@test.com", metadata={}, configuration="recipient")
        assert captured["dashboard"] == "express"
        assert captured["defaults"]["responsibilities"] == {"fees_collector": "application", "losses_collector": "application"}
