"""Integration tests for the ZR-WIR-TRACE-001 TR-11 Payments operational
metrics endpoint: app/services/payment_operational_metrics.py and the new
GET /api/analytics/payment-operational-metrics route.

Covers: an empty Payments domain reports null rates/averages (never a
ZeroDivisionError or a fabricated 0.0), and a small mixed scenario across
both the Listing Fee and rental payment record domains produces the exact
expected counts/rates."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.crud import listing_fee as lf_crud
from app.crud import rental_payment as rp_crud
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_rental_payment_records import _make_party
from tests.test_zr_pay_002_acceptance_gates import _make_listing, _make_listing_fee_policy


class TestEmptyPaymentsDomain:
    def test_empty_domain_reports_nulls_not_zeros_or_errors(self, client, db_session: Session):
        admin = _make_admin(db_session, email="pay-metrics-admin-empty@test.com", role="super_admin")
        r = client.get("/api/analytics/payment-operational-metrics", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["totalListingFeePayments"] == 0
        assert body["listingFeePaymentsByStatus"] == {}
        assert body["listingFeePaymentFailureRate"] is None
        assert body["avgListingFeeTimeToSuccessSeconds"] is None
        assert body["totalListingFeeRefunds"] == 0
        assert body["listingFeeRefundsByStatus"] == {}
        assert body["listingFeeRefundRate"] is None
        assert body["totalRentalPaymentObligations"] == 0
        assert body["obligationsByStatus"] == {}
        assert body["totalRentalPaymentRecords"] == 0
        assert body["recordsByStatus"] == {}
        assert body["avgRentalPaymentConfirmationTurnaroundDays"] is None
        assert body["rentalPaymentDiscrepancyRate"] is None
        assert body["paymentInstructionsPendingReviewCount"] == 0
        assert body["paymentInstructionsRejectedCount"] == 0

    def test_requires_super_admin(self, client, db_session: Session):
        admin = _make_admin(db_session, email="pay-metrics-admin-regular@test.com", role="admin")
        r = client.get("/api/analytics/payment-operational-metrics", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403


class TestMixedPaymentsScenario:
    def test_listing_fee_and_rental_payment_metrics(self, db_session: Session):
        # -- Listing Fee side: one succeeded payment (with a refund), one failed.
        party = _make_party(db_session, party_type="provider")
        listing1 = _make_listing(db_session, listing_id="L-METRICS-1", party_id=party.id)
        listing2 = _make_listing(db_session, listing_id="L-METRICS-2", party_id=party.id)
        _make_listing_fee_policy(db_session, refund_eligible=True)

        quote1 = lf_crud.create_quote(db_session, listing1, party)
        payment1, _ = lf_crud.create_checkout(db_session, quote1, party, idempotency_key="metrics-lf-1", billing_country="GB")
        assert payment1.status == "SUCCEEDED"

        from app.schemas.listing_fee import ListingFeeRefundCreate

        admin = _make_admin(db_session, email="pay-metrics-refund-admin@test.com", role="super_admin")
        lf_crud.request_refund(
            db_session, admin, payment1,
            ListingFeeRefundCreate(amount=float(payment1.amount), reason="test", idempotency_key="metrics-refund-1"),
        )

        quote2 = lf_crud.create_quote(db_session, listing2, party)
        from app.models.listing_fee import ListingFeePayment

        payment2 = ListingFeePayment(
            quote_id=quote2.id, listing_id=listing2.id, party_id=party.id, amount=float(quote2.total_amount),
            currency=quote2.currency, idempotency_key="metrics-lf-2", billing_country="GB",
            provider_payment_intent_id="pi_metrics_fail", status="FAILED", failure_message="card declined",
        )
        db_session.add(payment2)
        db_session.commit()

        # -- Rental payment side: one confirmed record, one disputed-then-open record.
        obligation1, tenant1, recipient1 = self._make_obligation(db_session)
        record1 = rp_crud.mark_paid(
            db_session, tenant1, obligation1, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.confirm_receipt(db_session, recipient1, record1)

        obligation2, tenant2, _recipient2 = self._make_obligation(db_session)
        record2 = rp_crud.mark_paid(
            db_session, tenant2, obligation2, amount=850, currency="GBP", declared_date=date.today(),
            payment_method_category="BANK_TRANSFER",
        )
        rp_crud.report_discrepancy(db_session, record=record2, reason_code="NOT_ARRIVED", reported_by_guest_id=tenant2.id)

        r = self.client_get_metrics(db_session)
        assert r["totalListingFeePayments"] == 2
        assert r["listingFeePaymentsByStatus"] == {"SUCCEEDED": 1, "FAILED": 1}
        assert r["listingFeePaymentFailureRate"] == 0.5
        assert r["avgListingFeeTimeToSuccessSeconds"] is not None
        assert r["totalListingFeeRefunds"] == 1
        assert r["listingFeeRefundRate"] == 1.0  # the one SUCCEEDED payment has a refund

        assert r["totalRentalPaymentObligations"] == 2
        assert r["totalRentalPaymentRecords"] == 2
        assert r["recordsByStatus"].get("CONFIRMED_BY_RECIPIENT") == 1
        assert r["recordsByStatus"].get("DISPUTED") == 1
        assert r["avgRentalPaymentConfirmationTurnaroundDays"] is not None
        # 1 of 2 records has ever had a dispute opened against it.
        assert r["rentalPaymentDiscrepancyRate"] == 0.5

    @staticmethod
    def _make_obligation(db: Session):
        from tests.test_rental_payment_records import _make_guest

        recipient = _make_party(db)
        counter = getattr(TestMixedPaymentsScenario, "_counter", 0) + 1
        TestMixedPaymentsScenario._counter = counter
        tenant = _make_guest(db, guest_id=f"G-METRICS-{counter}")
        obligation = rp_crud.create_obligation(
            db, obligation_type="RENT", tenant_guest_id=tenant.id, recipient_party_id=recipient.id,
            amount=850, currency="GBP", due_date=date.today(),
        )
        return obligation, tenant, recipient

    @staticmethod
    def client_get_metrics(db_session: Session) -> dict:
        from app.services.payment_operational_metrics import compute_payment_operational_metrics

        return compute_payment_operational_metrics(db_session).model_dump(by_alias=True)
