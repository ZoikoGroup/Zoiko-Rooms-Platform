"""ZR-ENG-CLR-005 AC-20/AC-35: a market pack's funds_flow_profile is resolved
from effective-dated policy (same resolver as fee rate/deposit custody), and
run_payout fails closed -- refusing the payout outright, not silently
defaulting to direct settlement or Zoiko custody -- when that profile isn't
one this build actually implements."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
from tests.conftest import auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


class TestUnsupportedFundsFlowProfileFailsClosed:
    def test_payout_is_refused_outright_for_an_unsupported_profile(self, client, db_session: Session):
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        policy.funds_flow_profile = "TRUST_ESCROW_CUSTODY"
        db_session.commit()

        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="ffp1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "ffp-1"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text
        assert "funds-flow" in r.text.lower()

    def test_direct_settlement_still_pays_out_normally(self, client, db_session: Session):
        # The default, unmodified policy (DIRECT_SETTLEMENT) must keep working.
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="ffp2", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "ffp-2"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAID"
