"""ZR-ENG-CLR-005 Section 6.4's admin console 'Timeline: Immutable normalized
events + raw webhook references + actor/system timestamps' panel --
GET /api/finance/payments/{id}/timeline (crud/finance.py:get_payment_timeline)
merges DomainEvent, AuditEvent and PaymentProviderEvent (via
ProcessorTransaction) into one chronological view instead of an admin having
to separately query three endpoints and merge them by hand."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_finance_payment_confirm_authz import _make_provider_a_obligation


class TestPaymentTimeline:
    def test_directly_confirmed_payment_shows_audit_and_domain_event_entries(self, client, db_session: Session):
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        admin_a_cookies = auth_admin_cookie(admin_a)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "timeline-key-1"},
            cookies=admin_a_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_a_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.get(f"/api/finance/payments/{payment_id}/timeline", cookies=admin_a_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["paymentId"] == payment_id
        sources = {e["source"] for e in body["entries"]}
        assert "AUDIT_EVENT" in sources
        assert "DOMAIN_EVENT" in sources

        audit_entry = next(e for e in body["entries"] if e["source"] == "AUDIT_EVENT")
        assert audit_entry["eventType"] == "payment.confirm"
        assert audit_entry["actor"] == f"admin:{admin_a.id}"

        domain_entry = next(e for e in body["entries"] if e["source"] == "DOMAIN_EVENT")
        assert domain_entry["eventType"] == "payment.succeeded"

        # Chronological order.
        timestamps = [e["timestamp"] for e in body["entries"]]
        assert timestamps == sorted(timestamps)

    def test_dispatched_and_webhook_confirmed_payment_shows_a_provider_webhook_entry(self, client, db_session: Session):
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        admin_a_cookies = auth_admin_cookie(admin_a)
        super_admin = _make_admin(db_session, email="timeline-super@test.com", role="super_admin")

        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "timeline-key-2"},
            cookies=admin_a_cookies,
        )
        payment_id = r.json()["id"]
        r = client.post(
            f"/api/finance/payments/{payment_id}/dispatch",
            json={"allocations": [{"obligationId": obligation.id, "amount": 500.0}]},
            cookies=admin_a_cookies,
        )
        provider_transaction_id = r.json()["providerTransactionId"]

        client.post(
            "/api/finance/payments/provider-callback/simulate",
            json={"providerEventId": "timeline-evt-1", "providerTransactionId": provider_transaction_id, "eventType": "PAYMENT_SUCCEEDED"},
            cookies=auth_admin_cookie(super_admin),
        )

        r = client.get(f"/api/finance/payments/{payment_id}/timeline", cookies=admin_a_cookies)
        assert r.status_code == 200, r.text
        entries = r.json()["entries"]
        webhook_entry = next(e for e in entries if e["source"] == "PROVIDER_WEBHOOK")
        assert webhook_entry["eventType"] == "PAYMENT_SUCCEEDED"
        assert webhook_entry["detail"]["providerEventId"] == "timeline-evt-1"

    def test_an_unrelated_admin_cannot_view_the_timeline(self, client, db_session: Session):
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "timeline-key-3"},
            cookies=auth_admin_cookie(admin_a),
        )
        payment_id = r.json()["id"]

        outsider = _make_admin(db_session, email="timeline-outsider@test.com", role="admin")
        r = client.get(f"/api/finance/payments/{payment_id}/timeline", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 403, r.text

    def test_super_admin_can_view_any_payments_timeline(self, client, db_session: Session):
        obligation, admin_a, guest = _make_provider_a_obligation(db_session)
        r = client.post(
            "/api/finance/payments",
            json={"guestId": guest.id, "amount": 500.0, "currency": "INR", "idempotencyKey": "timeline-key-4"},
            cookies=auth_admin_cookie(admin_a),
        )
        payment_id = r.json()["id"]

        super_admin = _make_admin(db_session, email="timeline-super2@test.com", role="super_admin")
        r = client.get(f"/api/finance/payments/{payment_id}/timeline", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text

    def test_timeline_for_a_nonexistent_payment_is_404(self, client, db_session: Session):
        admin = _make_admin(db_session, email="timeline-404@test.com", role="super_admin")
        r = client.get("/api/finance/payments/999999/timeline", cookies=auth_admin_cookie(admin))
        assert r.status_code == 404, r.text
