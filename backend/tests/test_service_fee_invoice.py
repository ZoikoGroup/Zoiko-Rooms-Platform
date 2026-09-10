"""ZR-ENG-CLR-005 Section 13.1/AC-26: run_payout generates an immutable
ServiceFeeInvoice best-effort (only when PAID); the invoice issuer is the
market pack's resolved Zoiko legal entity/tax rate (not hard-coded), frozen
at issuance so a later policy change doesn't retroactively alter an already-
issued invoice; the download route is idempotent and provider-scoped."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
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


class TestRunPayoutGeneratesAServiceFeeInvoice:
    def test_paid_payout_creates_exactly_one_invoice_with_resolved_entity_and_fee(self, client, db_session: Session):
        payout_id, _admin, _admin_cookies = _pay_and_run_payout(client, db_session, suffix="invgen1", amount=1000.0)

        invoices = db_session.scalars(select(ServiceFeeInvoice).where(ServiceFeeInvoice.payout_id == payout_id)).all()
        assert len(invoices) == 1
        invoice = invoices[0]
        assert invoice.invoice_number == f"INV-{payout_id:08d}"
        assert invoice.legal_entity_name == "Zoiko Realty Group"
        assert float(invoice.fee_amount) == 100.0  # 10% default rate on 1000
        assert float(invoice.tax_rate) == 0.0
        assert float(invoice.tax_amount) == 0.0

    def test_invoice_reflects_the_policys_configured_legal_entity_and_tax_rate(self, client, db_session: Session):
        policy = db_session.query(MarketPolicyPack).filter_by(jurisdiction_code="IN").one()
        policy.zoiko_legal_entity_name = "Zoiko Rooms India Pvt Ltd"
        policy.zoiko_tax_registration_number = "GSTIN123456"
        policy.service_fee_tax_rate = 0.18
        db_session.commit()

        payout_id, _admin, _admin_cookies = _pay_and_run_payout(client, db_session, suffix="invgen2", amount=1000.0)

        invoice = db_session.scalar(select(ServiceFeeInvoice).where(ServiceFeeInvoice.payout_id == payout_id))
        assert invoice.legal_entity_name == "Zoiko Rooms India Pvt Ltd"
        assert invoice.tax_registration_number == "GSTIN123456"
        assert float(invoice.tax_rate) == 0.18
        assert float(invoice.fee_amount) == 100.0
        assert float(invoice.tax_amount) == 18.0


class TestServiceFeeInvoiceDownload:
    def test_owning_admin_can_download_the_invoice_pdf(self, client, db_session: Session):
        payout_id, _admin, admin_cookies = _pay_and_run_payout(client, db_session, suffix="invdl1")

        r = client.get(f"/api/finance/payouts/{payout_id}/service-fee-invoice", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")
        assert "attachment" in r.headers["content-disposition"]

    def test_calling_it_twice_is_idempotent(self, client, db_session: Session):
        payout_id, _admin, admin_cookies = _pay_and_run_payout(client, db_session, suffix="invdl2")

        r1 = client.get(f"/api/finance/payouts/{payout_id}/service-fee-invoice", cookies=admin_cookies)
        r2 = client.get(f"/api/finance/payouts/{payout_id}/service-fee-invoice", cookies=admin_cookies)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.content == r2.content

        invoices = db_session.scalars(select(ServiceFeeInvoice).where(ServiceFeeInvoice.payout_id == payout_id)).all()
        assert len(invoices) == 1

    def test_other_providers_admin_cannot_download_it(self, client, db_session: Session):
        payout_id, _admin, _admin_cookies = _pay_and_run_payout(client, db_session, suffix="invdl3")
        outsider = _make_admin(db_session, email="inv-outsider@test.com", role="admin")

        r = client.get(f"/api/finance/payouts/{payout_id}/service-fee-invoice", cookies=auth_admin_cookie(outsider))
        assert r.status_code == 403, r.text
