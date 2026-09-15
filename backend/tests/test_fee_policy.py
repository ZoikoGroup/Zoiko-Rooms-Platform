"""ZR-ENG-CLR-005 AC-09: platform fees must resolve from an effective-dated
market policy pack, not a hard-coded rate. Confirms crud/finance.py::run_payout
actually uses MarketPolicyPack.platform_fee_rate -- proven by changing the
seeded policy's rate and checking the computed fee changes with it, not just
that the default (0.10, matching the old hard-coded constant) happens to
work."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
from tests.conftest import auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


class TestPayoutFeeResolvedFromPolicy:
    def test_changing_the_policy_rate_changes_the_computed_payout_fee(self, client, db_session: Session):
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        policy.platform_fee_rate = 0.20
        db_session.commit()

        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="feepolicy1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "fee-policy-1"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
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
        payout = r.json()

        # 20% rate, not the 10% default -- proves this came from the policy row.
        assert payout["amount"] == 800.0


class TestPayoutFeeUsesPeriodEffectiveDate:
    def test_a_later_rate_change_does_not_retroactively_apply_to_an_earlier_period(self, client, db_session: Session):
        """AC-34: a payout run late (after the fee policy changed) must still
        apply the rate in effect during the period actually being paid out,
        not whatever rate happens to be current when the payout runs."""
        # The seeded pack (version 1, effective_from=2026-01-01, rate 0.10)
        # stays in force for early periods; a version 2 raises the rate to
        # 0.30 starting mid-year -- well before "today" in this test env.
        db_session.add(MarketPolicyPack(
            jurisdiction_code="IN", version=2, effective_from=date(2026, 6, 1),
            confidence="REVIEW_REQUIRED", legal_source_note="Test: later rate change.",
            platform_fee_rate=0.30,
        ))
        db_session.commit()

        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="feepolicy2", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "fee-policy-2"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        # Paying out February's rent, run late (well after the June rate
        # change) -- must still use February's 10% rate, not the current 30%.
        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-02"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout = r.json()
        assert payout["amount"] == 900.0
