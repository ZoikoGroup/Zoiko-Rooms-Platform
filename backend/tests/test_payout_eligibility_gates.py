"""ZR-ENG-CLR-005 AC-19/Section 9.2: run_payout now blocks (HELD, not PAID)
when an obligation being paid out has an open chargeback dispute, or when
the provider already has an unresolved NEGATIVE_ACCOUNT_BALANCE hold --
giving the FinancialHold/DisputeCase machinery built in increments 6/7/10
actual payout-blocking teeth, not just visibility."""

from __future__ import annotations

from sqlalchemy import select
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


class TestExistingNegativeBalancePartiallyOffsetsPayout:
    """ZR-ENG-CLR-006 Section 15 waterfall tier 4: a too-small later payout
    used to block entirely (see git history) -- it now applies whatever
    partial offset this period's own net allows and still pays it out
    (Section 2 doctrine: 'Undisputed money should not be trapped
    unnecessarily' -- this period's own earned rent is undisputed and
    unrelated to the shortfall from an earlier period), leaving the
    recovery/hold open for the remainder."""

    def test_a_too_small_payout_partially_offsets_and_still_pays_out(self, client, db_session: Session):
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
        # SAME party -- gross 500, no commission (ZR-PAY-CFG-001) -> net 500,
        # far less than the 1000 still outstanding on the recovery from above. The payout still
        # goes out (PAID), but its entire net is retained as a partial offset.
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
        assert payout["status"] == "PAID"
        assert float(payout["amount"]) == 500.0
        assert float(payout["recoveryOffsetAmount"]) == 500.0

        from app.models.finance import FinancialHold, HostRecovery

        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))
        assert recovery.status == "OPEN"
        assert float(recovery.recovered_amount) == 500.0
        hold = db_session.get(FinancialHold, recovery.financial_hold_id)
        assert hold.status == "OPEN"

        # A third period, generous enough to cover the remaining 500, fully
        # settles the recovery and resolves the hold.
        obligation3, _admin3, guest3, _party_id3 = _make_provider_rent_obligation(
            db_session, suffix="gateneg3", amount=1000.0, owner_party_id=party_id,
        )
        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest3.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "gate-neg-3"},
            cookies=admin_cookies,
        )
        payment_id3 = r.json()["id"]
        client.post(
            f"/api/finance/payments/{payment_id3}/confirm",
            json={"allocations": [{"obligationId": obligation3.id, "amount": 1000.0}]},
            cookies=admin_cookies,
        )
        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-10"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        payout3 = r.json()
        assert payout3["status"] == "PAID"
        assert float(payout3["recoveryOffsetAmount"]) == 500.0

        db_session.refresh(recovery)
        db_session.refresh(hold)
        assert recovery.status == "RECOVERED"
        assert float(recovery.recovered_amount) == 1000.0
        assert hold.status == "RESOLVED"
