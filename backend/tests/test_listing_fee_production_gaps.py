"""Listing Fee production gaps closed: chargebacks alert Zoiko (the merchant),
out-of-order dispute events can't reopen a closed dispute, a refund that
fails after Stripe accepted it is marked FAILED (and can be retried), stuck
PROCESSING refunds are reconciled with Stripe, confirmed refunds get a
credit note, and new receipts/credit notes are numbered gap-free."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud import listing_fee as lf_crud
from app.models.listing_fee import ListingFeeRefund
from app.models.notification import Notification
from app.services import stripe_client
from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_listing_fee_production_hardening import (
    _charge_refunded_event,
    _dispute_event,
    _processing_refund,
    _succeeded_payment,
)


def _admin_notifications(db: Session, notification_type: str) -> int:
    return db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.notification_type == notification_type, Notification.recipient_type == "admin",
        )
    )


class TestChargebackAlerts:
    def test_super_admins_are_told_when_a_chargeback_opens_and_closes(self, db_session: Session):
        _make_admin(db_session, email="lfg-sa@test.com", role="super_admin")
        payment = _succeeded_payment(db_session, session_id="cs_lfg_dp", intent_id="pi_lfg_dp")
        lf_crud.ingest_stripe_webhook_event(
            db_session, _dispute_event("evt_lfg_dp1", event_type="charge.dispute.created", intent_id="pi_lfg_dp", status="needs_response"),
        )
        assert _admin_notifications(db_session, "listing_fee.dispute_open") >= 1
        lf_crud.ingest_stripe_webhook_event(
            db_session, _dispute_event("evt_lfg_dp2", event_type="charge.dispute.closed", intent_id="pi_lfg_dp", status="lost"),
        )
        assert _admin_notifications(db_session, "listing_fee.dispute_lost") >= 1
        db_session.refresh(payment)
        assert payment.dispute_status == "LOST"

    def test_a_late_opened_event_never_reopens_a_closed_dispute(self, db_session: Session):
        payment = _succeeded_payment(db_session, session_id="cs_lfg_order", intent_id="pi_lfg_order")
        lf_crud.ingest_stripe_webhook_event(
            db_session, _dispute_event("evt_lfg_o1", event_type="charge.dispute.closed", intent_id="pi_lfg_order", status="won"),
        )
        lf_crud.ingest_stripe_webhook_event(
            db_session, _dispute_event("evt_lfg_o2", event_type="charge.dispute.created", intent_id="pi_lfg_order", status="needs_response"),
        )
        db_session.refresh(payment)
        assert payment.dispute_status == "WON"
        assert lf_crud.listing_fee_is_paid(db_session, payment.listing_id)


def _refund_updated_event(event_id: str, *, refund_id: str, status: str) -> dict:
    return {
        "id": event_id, "type": "charge.refund.updated",
        "data": {"object": {"id": refund_id, "object": "refund", "status": status, "failure_reason": "expired_or_canceled_card"}},
    }


class TestRefundFailures:
    def test_a_refund_that_fails_later_is_marked_failed_and_can_be_retried(self, db_session: Session):
        _make_admin(db_session, email="lfg-sa2@test.com", role="super_admin")
        payment = _succeeded_payment(db_session, session_id="cs_lfg_rf", intent_id="pi_lfg_rf")
        payment.quote.policy_snapshot = {**(payment.quote.policy_snapshot or {}), "refund_eligible": True}
        db_session.commit()
        refund = _processing_refund(db_session, payment, float(payment.amount), "lfgrf")
        assert not lf_crud.is_listing_fee_payment_refund_eligible(db_session, payment)

        lf_crud.ingest_stripe_webhook_event(db_session, _refund_updated_event("evt_lfg_rf", refund_id="re_lfgrf", status="failed"))
        db_session.refresh(refund)
        assert refund.status == "FAILED"
        assert "canceled" in refund.failure_message
        assert _admin_notifications(db_session, "listing_fee.refund_failed") >= 1
        db_session.refresh(payment)
        assert lf_crud.is_listing_fee_payment_refund_eligible(db_session, payment)  # retry is possible again

    def test_a_stuck_processing_refund_is_settled_by_the_hourly_job(self, db_session: Session, monkeypatch):
        payment = _succeeded_payment(db_session, session_id="cs_lfg_stuck", intent_id="pi_lfg_stuck")
        refund = _processing_refund(db_session, payment, 5.0, "lfgstuck")
        refund.created_at = datetime.now(timezone.utc) - timedelta(hours=3)
        db_session.commit()
        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "retrieve_refund", lambda **_kw: {"status": "succeeded", "failure_reason": ""})

        assert lf_crud.reconcile_processing_refunds(db_session) == 1
        db_session.refresh(refund)
        assert refund.status in ("PARTIALLY_REFUNDED", "REFUNDED")


class TestTaxDocuments:
    def test_a_confirmed_refund_gets_a_credit_note_and_numbers_are_gap_free(self, client, db_session: Session):
        first = _succeeded_payment(db_session, session_id="cs_lfg_cn1", intent_id="pi_lfg_cn1")
        second = _succeeded_payment(db_session, session_id="cs_lfg_cn2", intent_id="pi_lfg_cn2")
        receipt_a = lf_crud.get_or_create_listing_fee_receipt(db_session, first)
        receipt_b = lf_crud.get_or_create_listing_fee_receipt(db_session, second)
        db_session.commit()
        a, b = int(receipt_a.receipt_number.split("-")[-1]), int(receipt_b.receipt_number.split("-")[-1])
        assert receipt_a.receipt_number.startswith("ZR-LF-") and b == a + 1

        refund = _processing_refund(db_session, first, float(first.amount), "lfgcn")
        lf_crud.ingest_stripe_webhook_event(
            db_session, _charge_refunded_event("evt_lfg_cn", intent_id="pi_lfg_cn1", amount_refunded_minor=round(float(first.amount) * 100)),
        )
        db_session.refresh(refund)
        assert refund.status == "REFUNDED"
        assert refund.credit_note_number and refund.credit_note_number.startswith("ZR-CN-")

        admin = _make_admin(db_session, email="lfg-cn-admin@test.com", role="super_admin")
        listed = client.get(f"/api/finance/listing-fees/payments/{first.id}/refunds", cookies=auth_admin_cookie(admin))
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["creditNoteNumber"] == refund.credit_note_number
        pdf = client.get(f"/api/finance/listing-fees/refunds/{refund.id}/credit-note", cookies=auth_admin_cookie(admin))
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")

    def test_no_credit_note_for_an_unconfirmed_refund(self, client, db_session: Session):
        payment = _succeeded_payment(db_session, session_id="cs_lfg_cn3", intent_id="pi_lfg_cn3")
        refund = _processing_refund(db_session, payment, 1.0, "lfgcn3")
        admin = _make_admin(db_session, email="lfg-cn3-admin@test.com", role="super_admin")
        r = client.get(f"/api/finance/listing-fees/refunds/{refund.id}/credit-note", cookies=auth_admin_cookie(admin))
        assert r.status_code == 409
        assert db_session.get(ListingFeeRefund, refund.id).credit_note_number is None
