"""ZR-ENG-CLR-006 Section 15 waterfall tier 3: PSP_BALANCE_RECOVERY -- a real
Stripe Transfer Reversal pulling money back from the host's own Connected
Account balance, tried automatically the instant decide_refund opens a
HostRecovery (crud/finance.py:_execute_psp_transfer_reversal) and retryable
manually via POST /host-recoveries/{id}/attempt-psp-recovery
(crud/finance.py:attempt_psp_recovery) once it becomes possible later. See
tests/test_host_recovery.py's TestAutomaticFuturePayoutOffset for tier 4 (the
adjacent, already-existing tier this one is tried before)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import FinancialHold, HostRecovery
from tests.conftest import auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _connect_stripe(client, admin_cookies, party_id: int) -> None:
    r = client.post(
        "/api/finance/host-stripe-accounts",
        json={"partyId": party_id, "country": "GB", "email": "host@example.com"},
        cookies=admin_cookies,
    )
    account_id = r.json()["id"]
    client.post(f"/api/finance/host-stripe-accounts/{account_id}/simulate-onboarding-complete", cookies=admin_cookies)


def _pay_and_payout(client, admin_cookies, *, obligation, guest, party_id, amount: float, period_key: str, idem: str):
    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest.id, "amount": amount, "currency": "INR", "idempotencyKey": idem},
        cookies=admin_cookies,
    )
    payment_id = r.json()["id"]
    client.post(
        f"/api/finance/payments/{payment_id}/confirm",
        json={"allocations": [{"obligationId": obligation.id, "amount": amount}]},
        cookies=admin_cookies,
    )
    r = client.post("/api/finance/payouts/run", json={"partyId": party_id, "periodKey": period_key}, cookies=admin_cookies)
    assert r.status_code == 200, r.text
    return payment_id, r.json()


class TestAutomaticPspRecoveryCascade:
    def test_a_stripe_connected_host_is_recovered_from_immediately_on_refund(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="psprec1", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)
        _connect_stripe(client, admin_cookies, party_id)

        _payment_id, payout = _pay_and_payout(
            client, admin_cookies, obligation=obligation, guest=guest, party_id=party_id,
            amount=1000.0, period_key="2026-09", idem="psprec1-pay",
        )
        assert payout["status"] == "PAID"
        assert payout["stripeTransferId"]

        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": _payment_id, "obligationId": obligation.id, "amount": 1000.0, "reason": "psp recovery test",
                "idempotencyKey": "psprec1-refund",
            },
            cookies=admin_cookies,
        )
        refund_id = r.json()["id"]
        r = client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))
        assert recovery is not None
        # Tier 3 must have already fired inside decide_refund itself, with no
        # separate admin action -- but only the 900.0 that actually reached
        # the host's Stripe balance (net of the 10% platform fee run_payout
        # already took and kept as Zoiko's own revenue, never transferred to
        # the host at all) is reversible; the recovery's own amount (1000.0,
        # this raw decide_refund path -- unlike execute_refund_entitlement --
        # never reverses the platform fee) stays partially outstanding.
        assert recovery.status == "OPEN"
        assert recovery.recovery_method == "PSP_BALANCE_RECOVERY"
        assert float(recovery.recovered_amount) == 900.0
        assert recovery.psp_reversal_id
        assert recovery.psp_reversal_id.startswith("TRR-")

        hold = db_session.get(FinancialHold, recovery.financial_hold_id)
        assert hold.status == "OPEN"

    def test_a_host_with_no_stripe_account_still_falls_through_to_open(self, client, db_session: Session):
        """No Stripe Connect account at all -- tier 3 must no-op, not error,
        leaving the recovery exactly as OPEN as it always was pre-tier-3."""
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="psprec2", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        _payment_id, payout = _pay_and_payout(
            client, admin_cookies, obligation=obligation, guest=guest, party_id=party_id,
            amount=1000.0, period_key="2026-09", idem="psprec2-pay",
        )
        assert payout["status"] == "PAID"
        assert payout["stripeTransferId"] is None

        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": _payment_id, "obligationId": obligation.id, "amount": 1000.0, "reason": "no stripe account",
                "idempotencyKey": "psprec2-refund",
            },
            cookies=admin_cookies,
        )
        refund_id = r.json()["id"]
        client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)

        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))
        assert recovery.status == "OPEN"
        assert recovery.recovery_method == ""
        assert recovery.psp_reversal_id is None


class TestManualPspRecoveryRetry:
    def test_retry_fails_with_no_stripe_account_connected(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="psprec3", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)

        _payment_id, payout = _pay_and_payout(
            client, admin_cookies, obligation=obligation, guest=guest, party_id=party_id,
            amount=1000.0, period_key="2026-09", idem="psprec3-pay",
        )
        assert payout["stripeTransferId"] is None

        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": _payment_id, "obligationId": obligation.id, "amount": 1000.0, "reason": "retry test",
                "idempotencyKey": "psprec3-refund",
            },
            cookies=admin_cookies,
        )
        refund_id = r.json()["id"]
        client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))
        assert recovery.status == "OPEN"

        # No Stripe account was ever connected for this party -- tier 3
        # cannot apply, whether tried automatically or retried manually.
        r = client.post(f"/api/finance/host-recoveries/{recovery.id}/attempt-psp-recovery", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_manual_retry_recovers_whatever_the_automatic_cascade_left_outstanding(self, client, db_session: Session):
        """Same setup as TestAutomaticPspRecoveryCascade's Stripe-connected
        case: decide_refund's own cascade already reverses the 900.0 the
        host's Stripe balance actually received, leaving 100.0 outstanding
        (the platform fee this raw refund path never reverses -- see that
        test's own comment). A manual retry immediately after has the same
        transfer still available to reverse against for the remainder."""
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="psprec4", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)
        _connect_stripe(client, admin_cookies, party_id)

        _payment_id, _payout = _pay_and_payout(
            client, admin_cookies, obligation=obligation, guest=guest, party_id=party_id,
            amount=1000.0, period_key="2026-09", idem="psprec4-pay",
        )
        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": _payment_id, "obligationId": obligation.id, "amount": 1000.0, "reason": "manual retry test",
                "idempotencyKey": "psprec4-refund",
            },
            cookies=admin_cookies,
        )
        refund_id = r.json()["id"]
        client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))
        assert recovery.status == "OPEN"
        assert float(recovery.recovered_amount) == 900.0

        r = client.post(f"/api/finance/host-recoveries/{recovery.id}/attempt-psp-recovery", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "RECOVERED"
        assert float(body["recoveredAmount"]) == 1000.0
        assert body["recoveryMethod"] == "PSP_BALANCE_RECOVERY"
        assert body["pspReversalId"]

        hold = db_session.get(FinancialHold, recovery.financial_hold_id)
        assert hold.status == "RESOLVED"

    def test_retry_on_an_already_recovered_recovery_is_rejected(self, client, db_session: Session):
        obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix="psprec5", amount=1000.0)
        admin_cookies = auth_admin_cookie(admin)
        _connect_stripe(client, admin_cookies, party_id)

        _payment_id, _payout = _pay_and_payout(
            client, admin_cookies, obligation=obligation, guest=guest, party_id=party_id,
            amount=1000.0, period_key="2026-09", idem="psprec5-pay",
        )
        r = client.post(
            "/api/finance/refunds",
            json={
                "paymentId": _payment_id, "obligationId": obligation.id, "amount": 1000.0, "reason": "already recovered",
                "idempotencyKey": "psprec5-refund",
            },
            cookies=admin_cookies,
        )
        refund_id = r.json()["id"]
        client.post(f"/api/finance/refunds/{refund_id}/decide", json={"approve": True}, cookies=admin_cookies)
        recovery = db_session.scalar(select(HostRecovery).where(HostRecovery.party_id == party_id))

        # Fully recover it first (the automatic cascade only got to 900.0).
        r = client.post(f"/api/finance/host-recoveries/{recovery.id}/attempt-psp-recovery", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "RECOVERED"

        r = client.post(f"/api/finance/host-recoveries/{recovery.id}/attempt-psp-recovery", cookies=admin_cookies)
        assert r.status_code == 409, r.text
