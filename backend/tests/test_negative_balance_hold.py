"""ZR-ENG-CLR-005 Section 21 'Negative Host balance': refunding an obligation
that's already been paid out drives HOST_PAYABLE negative -- a real clawback.
decide_refund now surfaces this via a FinancialHold (reason_code
NEGATIVE_ACCOUNT_BALANCE, severity HIGH) instead of leaving it invisible,
without inventing an actual reserve/offset/collection policy (the spec says
that needs separate approval)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import FinancialHold
from tests.conftest import auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


class TestRefundAfterPayoutFlagsNegativeBalance:
    def test_refunding_an_already_paid_out_obligation_creates_a_high_severity_hold(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="negbal1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 1000.0, "currency": "INR", "idempotencyKey": "negbal-pay-1"},
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

        # Payout runs and pays the host in full before the refund lands.
        r = client.post(
            "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PAID"

        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 1000.0, "reason": "test clawback",
                "idempotencyKey": "negbal-refund-request-1",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        refund_id = r.json()["id"]

        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        hold = db_session.scalar(select(FinancialHold).where(FinancialHold.reason_code == "NEGATIVE_ACCOUNT_BALANCE"))
        assert hold is not None
        assert hold.severity == "HIGH"
        assert hold.status == "OPEN"
        assert hold.source_type == "ledger_account"

    def test_refunding_before_payout_does_not_flag_anything(self, client, db_session: Session):
        # Same flow, but no payout runs first -- HOST_PAYABLE never goes
        # negative (it just returns to zero), so no hold should be created.
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="negbal2", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "negbal-pay-2"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": payment_id, "obligationId": obligation.id, "amount": 500.0, "reason": "test no clawback",
                "idempotencyKey": "negbal-refund-request-2",
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        refund_id = r.json()["id"]
        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        assert db_session.scalars(select(FinancialHold)).all() == []
