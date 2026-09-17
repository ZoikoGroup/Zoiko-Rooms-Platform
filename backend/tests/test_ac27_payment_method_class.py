"""ZR-ENG-CLR-005 AC-27 'Host cannot manually mark a renter obligation paid
without a controlled external-payment workflow' + Section 12.1's normalized
payment-method-class taxonomy. Before this, crud/finance.py:confirm_payment
never distinguished a genuinely off-platform (EXTERNAL) payment from one
already dispatched to a real PSP -- a Host's own owning admin could directly
confirm ANY payment, including one asserting a real card/bank rail was
used, completely bypassing app/crud/payment_provider.py's dispatch/webhook
pipeline. method_class (default EXTERNAL) plus confirm_payment's own gate
closes that: only EXTERNAL payments are confirmable directly by their owning
admin; anything else requires the real provider callback (which always acts
as the system/super_admin, see payment_provider.get_system_admin)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.finance import SimulatedPayment
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_finance_payment_confirm_authz import _make_provider_a_obligation


class TestOwningHostCannotDirectlyConfirmAPspDispatchedPayment:
    def test_direct_confirm_is_rejected_once_the_payment_is_dispatched(self, client, db_session: Session):
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        admin_a_cookies = auth_admin_cookie(admin_a)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "ac27-key-1"},
            cookies=admin_a_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]
        assert r.json()["methodClass"] == "EXTERNAL"

        r = client.post(
            f"/api/finance/payments/{payment_id}/dispatch",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_a_cookies,
        )
        assert r.status_code == 200, r.text

        payment = db_session.get(SimulatedPayment, payment_id)
        assert payment.method_class == "CARD"

        # The exact AC-27 attack: the owning Host tries to just mark it paid
        # themselves instead of waiting for the real provider callback.
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_a_cookies,
        )
        assert r.status_code == 403, r.text
        db_session.refresh(obligation)
        assert obligation.status == "PENDING"

        # A regular admin simulating the provider webhook on their own
        # dispatched payment is blocked the same way -- it isn't a separate
        # bypass route.
        r = client.post(
            "/api/finance/payments/provider-callback/simulate",
            json={"providerEventId": "ac27-evt-1", "providerTransactionId": _dispatched_transaction_id(db_session, payment_id), "eventType": "PAYMENT_SUCCEEDED"},
            cookies=admin_a_cookies,
        )
        assert r.status_code == 403, r.text

    def test_super_admin_can_complete_it_via_the_real_callback(self, client, db_session: Session):
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        admin_a_cookies = auth_admin_cookie(admin_a)
        super_admin = _make_admin(db_session, email="ac27-super@test.com", role="super_admin")
        super_admin_cookies = auth_admin_cookie(super_admin)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "ac27-key-2"},
            cookies=admin_a_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/dispatch",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_a_cookies,
        )
        provider_transaction_id = r.json()["providerTransactionId"]

        r = client.post(
            "/api/finance/payments/provider-callback/simulate",
            json={"providerEventId": "ac27-evt-2", "providerTransactionId": provider_transaction_id, "eventType": "PAYMENT_SUCCEEDED"},
            cookies=super_admin_cookies,
        )
        assert r.status_code == 200, r.text

        payment = db_session.get(SimulatedPayment, payment_id)
        assert payment.status == "SUCCEEDED"

    def test_owning_host_can_still_confirm_a_genuinely_external_payment(self, client, db_session: Session):
        """The one class a direct confirm was always meant to serve --
        Section 12.1's off-platform cash/cheque recording."""
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        admin_a_cookies = auth_admin_cookie(admin_a)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "ac27-key-3"},
            cookies=admin_a_cookies,
        )
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_a_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUCCEEDED"


def _dispatched_transaction_id(db: Session, payment_id: int) -> str:
    from app.models.finance import ProcessorTransaction

    txn = db.query(ProcessorTransaction).filter(ProcessorTransaction.payment_id == payment_id).first()
    return txn.provider_transaction_id


class TestUnrecognizedMethodClassIsRejected:
    def test_creating_a_payment_with_a_bogus_method_class_is_rejected(self, client, db_session: Session):
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        r = client.post(
            "/api/finance/payments",
            json={
                "guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "ac27-key-4",
                "methodClass": "BITCOIN",
            },
            cookies=auth_admin_cookie(admin_a),
        )
        assert r.status_code == 400, r.text
