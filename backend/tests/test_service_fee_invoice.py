"""ZR-ENG-CLR-005 Section 13.1/AC-26: run_payout generates an immutable
ServiceFeeInvoice best-effort (only when PAID); the invoice issuer is the
market pack's resolved Zoiko legal entity/tax rate (not hard-coded), frozen
at issuance so a later policy change doesn't retroactively alter an already-
issued invoice; the download route is idempotent and provider-scoped."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import ServiceFeeInvoice
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_ledger import _make_provider_rent_obligation


def _pay_and_run_payout(client, db_session: Session, *, suffix: str, amount: float = 1000.0):
    obligation, admin, guest, party_id = _make_provider_rent_obligation(db_session, suffix=suffix, amount=amount)
    admin_cookies = auth_admin_cookie(admin)

    r = client.post(
        "/api/finance/payments",
        json={"guestId": guest.id, "amount": amount, "currency": "INR", "idempotencyKey": f"inv-{suffix}"},
        cookies=admin_cookies,
    )
    payment_id = r.json()["id"]
    r = client.post(
        f"/api/finance/payments/{payment_id}/confirm",
        json={"allocations": [{"obligationId": obligation.id, "amount": amount}]},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/finance/payouts/run", json={"partyId": party_id, "periodKey": "2026-09"}, cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"], admin, admin_cookies


class TestNoServiceFeeInvoice:
    """ZR-PAY-CFG-001 Decision 3: 'Do not calculate, display, invoice, accrue,
    settle or report a rental commission.' Replaces the earlier ZR-ENG-CLR-005
    AC-26 tests -- a payout no longer issues a service-fee invoice, and the
    download route reports there is none."""

    def test_paid_payout_creates_no_invoice(self, client, db_session: Session):
        payout_id, _admin, _admin_cookies = _pay_and_run_payout(client, db_session, suffix="noinv1", amount=1000.0)
        invoices = db_session.scalars(select(ServiceFeeInvoice).where(ServiceFeeInvoice.payout_id == payout_id)).all()
        assert invoices == []

    def test_download_route_reports_no_invoice(self, client, db_session: Session):
        payout_id, _admin, admin_cookies = _pay_and_run_payout(client, db_session, suffix="noinv2", amount=1000.0)
        r = client.get(f"/api/finance/payouts/{payout_id}/service-fee-invoice", cookies=admin_cookies)
        assert r.status_code == 404, r.text
        assert "no commission" in r.json()["detail"]
