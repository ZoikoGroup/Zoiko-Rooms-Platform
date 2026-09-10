"""ZR-ENG-CLR-005 AC-19/Section 9.2: run_payout now blocks (HELD, not PAID)
when an obligation being paid out has an open chargeback dispute, or when
the provider already has an unresolved NEGATIVE_ACCOUNT_BALANCE hold --
giving the FinancialHold/DisputeCase machinery built in increments 6/7/10
actual payout-blocking teeth, not just visibility."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


class TestOpenChargebackBlocksPayout:
    def test_payout_is_held_when_an_obligation_has_an_open_chargeback(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="gatecb1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "gate-cb-1"},
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
            "/api/finance/disputes",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 1000.0,
                "category": "CHARGEBACK", "description": "card issuer opened a chargeback",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout = r.json()
        assert payout["status"] == "HELD"
        assert "chargeback" in payout["holdReason"].lower()


class TestExistingNegativeBalanceBlocksPayout:
    def test_a_new_payout_is_held_while_the_party_has_an_unresolved_negative_balance(self, client, db_session: Session):
        # Reproduce the increment-7 clawback scenario first: pay, pay out,
        # then refund -- driving HOST_PAYABLE negative and opening a hold.
        obligation1, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="gateneg1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "gate-neg-1"},
            cookies=admin_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation1.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-08"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAID"

        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": payment_id, "obligationId": obligation1.id, "amount": 1000.0, "reason": "clawback",
                "idempotencyKey": "gate-neg-refund-1",
            },
            cookies=admin_cookies,
        )
        refund_id = r.json()["id"]
        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        # Now a second, otherwise-perfectly-normal obligation/payment for the
        # SAME party -- a *new* payout run must still be held because the
        # negative-balance hold from the refund above is still OPEN.
        obligation2, _admin2, guest2, _party_id2 = _make_provider_rent_obligation(
            db_session, suffix="gateneg2", amount=500.0, owner_party_id=party_id,
        )
        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest2.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "gate-neg-2"},
            cookies=admin_cookies,
        )
        payment_id2 = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id2}/confirm",
            json={"allocations": [{"obligationId": obligation2.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout = r.json()
        assert payout["status"] == "HELD"
        assert "negative" in payout["holdReason"].lower()
