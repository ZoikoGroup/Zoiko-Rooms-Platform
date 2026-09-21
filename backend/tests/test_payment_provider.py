"""ZR-ENG-CLR-005 Section 3.1/9.1/17.1: the payment-provider dispatch +
callback ingestion pipeline (app/crud/payment_provider.py). Mirrors
tests/test_agreement_engine_extensions_2.py::TestSignatureProviderAC23AC24
exactly -- idempotent authenticated callback ingestion + outage-safe
reconciliation against the simulated provider, additive alongside the
pre-existing direct create_payment_intent/confirm_payment path."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from sqlalchemy import select

from app.models.finance import Obligation, ProcessorTransaction, SimulatedPayment
from app.models.notification import Notification
from tests.conftest import _make_admin, _make_user, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _create_pending_payment(client, admin_cookies, guest_id: str, amount: float, currency: str, key: str) -> int:
    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest_id, "amount": amount, "currency": currency, "idempotencyKey": key},
        cookies=admin_cookies,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestDispatchPaymentToProvider:
    def test_dispatch_creates_a_pending_processor_transaction(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pp-dispatch1", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        payment_id = _create_pending_payment(client, admin_cookies, guest.id, 500.0, obligation.currency, "pp-dispatch1")

        r = client.post(
            f"/api/finance/payments/{payment_id}/dispatch",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "PENDING"
        assert body["providerTransactionId"]
        assert body["declaredAllocations"] == {str(obligation.id): 500.0}

    def test_cannot_dispatch_a_second_time_while_one_is_pending(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pp-dispatch2", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        payment_id = _create_pending_payment(client, admin_cookies, guest.id, 500.0, obligation.currency, "pp-dispatch2")

        r1 = client.post(
            f"/api/finance/payments/{payment_id}/dispatch",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r1.status_code == 200, r1.text

        r2 = client.post(
            f"/api/finance/payments/{payment_id}/dispatch",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r2.status_code == 409, r2.text

    def test_dispatch_fails_closed_during_outage(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pp-outage1", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        payment_id = _create_pending_payment(client, admin_cookies, guest.id, 500.0, obligation.currency, "pp-outage1")

        r = client.post("/api/finance/payment-provider/health", json={"healthy": False}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["healthy"] is False

        r = client.post(
            f"/api/finance/payments/{payment_id}/dispatch",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 503, r.text

        # Restore health so later tests in this module aren't affected by
        # a shared singleton row.
        client.post("/api/finance/payment-provider/health", json={"healthy": True}, cookies=admin_cookies)


class TestIngestProviderCallback:
    def _dispatch(self, client, admin_cookies, payment_id, obligation_id, amount):
        r = client.post(
            f"/api/finance/payments/{payment_id}/dispatch",
            json={"allocations": [{"obligationId": obligation_id, "amount": amount}]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        return r.json()

    def test_callback_completes_payment_and_is_idempotent(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pp-callback1", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        payment_id = _create_pending_payment(client, admin_cookies, guest.id, 500.0, obligation.currency, "pp-callback1")
        dispatched = self._dispatch(client, admin_cookies, payment_id, obligation.id, 500.0)

        payload = {
            "providerEventId": "pay-evt-001", "providerTransactionId": dispatched["providerTransactionId"],
            "eventType": "PAYMENT_SUCCEEDED",
        }
        r = client.post("/api/finance/payments/provider-callback/simulate", json=payload, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUCCEEDED"

        payment = db_session.get(SimulatedPayment, payment_id)
        assert payment.status == "SUCCEEDED"
        db_session.refresh(obligation)
        assert obligation.status == "PAID"

        # Replay the same event -- idempotent no-op, not double-allocated.
        r2 = client.post("/api/finance/payments/provider-callback/simulate", json=payload, cookies=admin_cookies)
        assert r2.status_code == 200, r2.text
        allocations = list(obligation.allocations)
        assert len(allocations) == 1

    def test_callback_reports_failure_without_touching_the_obligation(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pp-callback2", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        payment_id = _create_pending_payment(client, admin_cookies, guest.id, 500.0, obligation.currency, "pp-callback2")
        dispatched = self._dispatch(client, admin_cookies, payment_id, obligation.id, 500.0)

        payload = {
            "providerEventId": "pay-evt-fail-001", "providerTransactionId": dispatched["providerTransactionId"],
            "eventType": "PAYMENT_FAILED",
        }
        r = client.post("/api/finance/payments/provider-callback/simulate", json=payload, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "FAILED"

        payment = db_session.get(SimulatedPayment, payment_id)
        assert payment.status == "FAILED"
        db_session.refresh(obligation)
        assert obligation.status == "PENDING"

    def test_a_failed_payment_notifies_the_renter(self, client, db_session: Session):
        """Section 11 gap: the success path already notifies the renter --
        nothing did on failure, at either FAILED-transition site."""
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pp-notify1", amount=500.0)
        renter = _make_user(db_session, email=guest.email)
        guest.user_account_id = renter.id
        db_session.commit()
        admin_cookies = auth_admin_cookie(admin)
        payment_id = _create_pending_payment(client, admin_cookies, guest.id, 500.0, obligation.currency, "pp-notify1")
        dispatched = self._dispatch(client, admin_cookies, payment_id, obligation.id, 500.0)

        payload = {
            "providerEventId": "pay-evt-fail-notify-001", "providerTransactionId": dispatched["providerTransactionId"],
            "eventType": "PAYMENT_FAILED",
        }
        r = client.post("/api/finance/payments/provider-callback/simulate", json=payload, cookies=admin_cookies)
        assert r.status_code == 200, r.text

        notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == renter.id, Notification.notification_type == "payment.failed",
            )
        )
        assert notification is not None
        assert notification.related_entity_id == str(payment_id)

    def test_unknown_provider_transaction_id_is_404(self, client, db_session: Session):
        admin = _make_admin(db_session, email="pp-unknown-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        payload = {"providerEventId": "evt-x", "providerTransactionId": "PAYTXN-NOPE", "eventType": "PAYMENT_SUCCEEDED"}
        r = client.post("/api/finance/payments/provider-callback/simulate", json=payload, cookies=admin_cookies)
        assert r.status_code == 404, r.text


class TestReconcileStalledPayments:
    def test_reconcile_marks_stalled_transactions_failed_never_auto_completes(self, client, db_session: Session):
        obligation, admin, guest, _party_id = _make_provider_rent_obligation(db_session, suffix="pp-reconcile1", amount=500.0)
        admin_cookies = auth_admin_cookie(admin)
        payment_id = _create_pending_payment(client, admin_cookies, guest.id, 500.0, obligation.currency, "pp-reconcile1")

        r = client.post(
            f"/api/finance/payments/{payment_id}/dispatch",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_cookies,
        )
        txn_id = r.json()["id"]

        txn = db_session.get(ProcessorTransaction, txn_id)
        txn.dispatch_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        # Healthy provider -- reconcile is a no-op even past deadline (only
        # meant to run during a genuine outage).
        r = client.post("/api/finance/payments/reconcile-stalled", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["failedCount"] == 0

        client.post("/api/finance/payment-provider/health", json={"healthy": False}, cookies=admin_cookies)
        r = client.post("/api/finance/payments/reconcile-stalled", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["failedCount"] == 1
        assert r.json()["processorTransactionIds"] == [txn_id]

        db_session.refresh(txn)
        assert txn.status == "FAILED"
        payment = db_session.get(SimulatedPayment, payment_id)
        assert payment.status == "FAILED"
        db_session.refresh(obligation)
        assert obligation.status == "PENDING"

        client.post("/api/finance/payment-provider/health", json={"healthy": True}, cookies=admin_cookies)
