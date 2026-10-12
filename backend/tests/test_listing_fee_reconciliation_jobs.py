"""Listing Fee production fixes: a checkout whose webhook never arrived is
settled by the hourly job, a webhook that fails halfway is retried by Stripe
instead of being skipped as a duplicate, a refund whose create call timed out
is found again rather than marked failed, and no refund is sent on top of a
chargeback."""

from __future__ import annotations

import typing
from datetime import datetime, timedelta, timezone

import pytest
import stripe
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import listing_fee as lf_crud
from app.models.listing_fee import ListingFeeProviderEvent, ListingFeeRefund
from app.schemas.listing_fee import ListingFeeRefundCreate
from app.services import stripe_client
from tests.conftest import _make_admin
from tests.test_listing_fee_checkout_session_webhook import _make_pending_checkout_session_payment
from tests.test_listing_fee_production_hardening import _succeeded_payment


@pytest.fixture()
def db(db_engine) -> typing.Generator[Session, None, None]:
    """Like db_session, but the code's own rollbacks only undo their savepoint
    -- the paths under test roll back on purpose."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


def _age(db: Session, row, *, minutes: int) -> None:
    row.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    db.commit()


class TestPendingCheckoutSweep:
    def test_paid_and_expired_sessions_are_settled_and_recent_ones_left_alone(self, db: Session, monkeypatch):
        paid, _ = _make_pending_checkout_session_payment(db, checkout_session_id="cs_sweep_paid")
        expired, _ = _make_pending_checkout_session_payment(db, checkout_session_id="cs_sweep_expired")
        recent, _ = _make_pending_checkout_session_payment(db, checkout_session_id="cs_sweep_recent")
        _age(db, paid, minutes=90)
        _age(db, expired, minutes=90)

        sessions = {
            "cs_sweep_paid": {"payment_status": "paid", "payment_intent_id": "pi_sweep_paid", "status": "complete", "url": ""},
            "cs_sweep_expired": {"payment_status": "unpaid", "payment_intent_id": None, "status": "expired", "url": ""},
        }
        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "retrieve_checkout_session", lambda *, checkout_session_id: sessions[checkout_session_id])

        assert lf_crud.reconcile_pending_checkouts(db) == 2
        for row in (paid, expired, recent):
            db.refresh(row)
        assert paid.status == "SUCCEEDED" and paid.provider_payment_intent_id == "pi_sweep_paid"
        assert expired.status == "FAILED"
        assert recent.status == "PENDING"

    def test_a_late_failure_event_never_overwrites_a_succeeded_payment(self, db: Session):
        payment = _succeeded_payment(db, session_id="cs_sweep_late", intent_id="pi_sweep_late")
        lf_crud.ingest_stripe_webhook_event(db, {
            "id": "evt_sweep_late", "type": "checkout.session.async_payment_failed",
            "data": {"object": {"id": "cs_sweep_late"}},
        })
        db.refresh(payment)
        assert payment.status == "SUCCEEDED"


class TestWebhookRetry:
    def test_a_webhook_that_fails_after_payment_is_processed_again_on_retry(self, db: Session, monkeypatch):
        payment, _ = _make_pending_checkout_session_payment(db, checkout_session_id="cs_retry")
        event = {
            "id": "evt_retry", "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_retry", "payment_status": "paid", "payment_intent": "pi_retry"}},
        }
        calls = []

        def flaky_publish(_db, _payment, *, correlation_id=""):
            calls.append(correlation_id)
            if len(calls) == 1:
                raise RuntimeError("publish blew up")

        monkeypatch.setattr(lf_crud, "_publish_approved_listing_after_fee", flaky_publish)

        with pytest.raises(RuntimeError):
            lf_crud.ingest_stripe_webhook_event(db, event)
        db.refresh(payment)
        assert payment.status == "SUCCEEDED"  # the money side is kept
        assert db.query(ListingFeeProviderEvent).filter_by(provider_event_id="evt_retry").first() is None

        lf_crud.ingest_stripe_webhook_event(db, event)  # Stripe's retry
        assert len(calls) == 2  # the publish step ran again
        assert db.query(ListingFeeProviderEvent).filter_by(provider_event_id="evt_retry").first() is not None


def _refundable(db: Session, key: str):
    payment = _succeeded_payment(db, session_id=f"cs_{key}", intent_id=f"pi_{key}")
    admin = _make_admin(db, email=f"{key}@test.com", role="super_admin")
    return payment, admin


class TestRefundSafety:
    def test_no_refund_on_top_of_an_open_chargeback(self, db: Session):
        payment, admin = _refundable(db, "rf_dispute")
        payment.dispute_status = "OPEN"
        db.commit()
        with pytest.raises(HTTPException) as exc:
            lf_crud.request_refund(db, admin, payment, ListingFeeRefundCreate(amount=1.0, idempotency_key="rf-dispute-1"))
        assert exc.value.status_code == 409
        assert not lf_crud.is_listing_fee_payment_refund_eligible(db, payment)

    def test_a_timed_out_refund_stays_requested_and_is_found_again_by_the_job(self, db: Session, monkeypatch):
        payment, admin = _refundable(db, "rf_timeout")
        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)

        def timeout(**_kw):
            raise stripe.APIConnectionError("read timed out")

        monkeypatch.setattr(stripe_client, "create_refund", timeout)
        refund = lf_crud.request_refund(db, admin, payment, ListingFeeRefundCreate(amount=2.0, idempotency_key="rf-timeout-1"))
        assert refund.status == "REQUESTED"  # not FAILED -- Stripe may have made it

        # The remaining amount already accounts for it, so a retry can't double up.
        with pytest.raises(HTTPException):
            lf_crud.request_refund(
                db, admin, payment,
                ListingFeeRefundCreate(amount=float(payment.amount), idempotency_key="rf-timeout-2"),
            )

        _age(db, refund, minutes=30)
        monkeypatch.setattr(stripe_client, "find_refund_by_metadata", lambda **kw: (
            {"id": "re_found", "status": "succeeded", "failure_reason": ""}
            if kw["value"] == str(refund.id) else None
        ))
        monkeypatch.setattr(stripe_client, "retrieve_amount_refunded", lambda **_kw: 200)
        assert lf_crud.reconcile_processing_refunds(db) == 1
        db.refresh(refund)
        assert refund.provider_refund_id == "re_found"
        assert refund.status in ("PARTIALLY_REFUNDED", "REFUNDED")

    def test_a_timed_out_refund_stripe_never_made_is_sent_with_the_same_key(self, db: Session, monkeypatch):
        payment, admin = _refundable(db, "rf_resend")
        refund = ListingFeeRefund(
            payment_id=payment.id, amount=1.5, currency=payment.currency, reason="t", idempotency_key="rf-resend-1",
            requested_by_admin_id=admin.id, status="REQUESTED",
        )
        db.add(refund)
        db.commit()
        _age(db, refund, minutes=30)

        sent = {}
        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "find_refund_by_metadata", lambda **_kw: None)
        monkeypatch.setattr(stripe_client, "retrieve_amount_refunded", lambda **_kw: 0)
        monkeypatch.setattr(stripe_client, "create_refund", lambda **kw: sent.update(kw) or "re_resent")
        monkeypatch.setattr(stripe_client, "retrieve_refund", lambda **_kw: {"status": "pending", "failure_reason": ""})

        assert lf_crud.reconcile_processing_refunds(db) == 1
        db.refresh(refund)
        assert sent["idempotency_key"] == "listing_fee_refund:rf-resend-1"
        assert refund.status == "PROCESSING" and refund.provider_refund_id == "re_resent"

    def test_a_dashboard_refund_skipped_while_ours_was_processing_is_recorded_later(self, db: Session, monkeypatch):
        payment, admin = _refundable(db, "rf_dash")
        _make_admin(db, email=settings.seed_admin_email, role="super_admin")  # records the dashboard refund
        total_minor = round(float(payment.amount) * 100)
        ours = ListingFeeRefund(
            payment_id=payment.id, amount=1.0, currency=payment.currency, reason="t", idempotency_key="rf-dash-1",
            requested_by_admin_id=admin.id, status="PROCESSING", provider_refund_id="re_ours",
        )
        db.add(ours)
        db.commit()
        _age(db, ours, minutes=180)

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "retrieve_refund", lambda **_kw: {"status": "succeeded", "failure_reason": ""})
        monkeypatch.setattr(stripe_client, "retrieve_amount_refunded", lambda **_kw: total_minor)  # rest refunded in the dashboard

        assert lf_crud.reconcile_processing_refunds(db) == 1
        db.refresh(payment)
        assert lf_crud._confirmed_refund_total(payment) == lf_crud._round2(float(payment.amount))
        assert not lf_crud.listing_fee_is_paid(db, payment.listing_id)


class TestReconcileGuards:
    def test_an_old_unsent_refund_is_failed_for_an_admin_not_sent(self, db: Session, monkeypatch):
        payment, admin = _refundable(db, "rf_old")
        refund = ListingFeeRefund(
            payment_id=payment.id, amount=1.0, currency=payment.currency, reason="t", idempotency_key="rf-old-1",
            requested_by_admin_id=admin.id, status="REQUESTED",
        )
        db.add(refund)
        db.commit()
        _age(db, refund, minutes=60 * 48)

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "find_refund_by_metadata", lambda **_kw: None)
        monkeypatch.setattr(stripe_client, "retrieve_amount_refunded", lambda **_kw: 0)
        monkeypatch.setattr(stripe_client, "create_refund", lambda **_kw: pytest.fail("must not send an old refund"))

        assert lf_crud.reconcile_processing_refunds(db) == 1
        db.refresh(refund)
        assert refund.status == "FAILED" and refund.provider_refund_id is None

    def test_an_unsent_refund_already_covered_by_another_is_not_sent(self, db: Session, monkeypatch):
        payment, admin = _refundable(db, "rf_covered")
        for key, status, provider_id in (("rf-cov-old", "REQUESTED", None), ("rf-cov-new", "REFUNDED", "re_cov_new")):
            db.add(ListingFeeRefund(
                payment_id=payment.id, amount=float(payment.amount), currency=payment.currency, reason="t",
                idempotency_key=key, requested_by_admin_id=admin.id, status=status, provider_refund_id=provider_id,
            ))
        db.commit()
        stale = db.query(ListingFeeRefund).filter_by(idempotency_key="rf-cov-old").one()
        _age(db, stale, minutes=30)

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "find_refund_by_metadata", lambda **_kw: None)
        monkeypatch.setattr(stripe_client, "retrieve_amount_refunded", lambda **_kw: round(float(payment.amount) * 100))
        monkeypatch.setattr(stripe_client, "create_refund", lambda **_kw: pytest.fail("would over-refund"))

        lf_crud.reconcile_processing_refunds(db)
        db.refresh(stale)
        assert stale.status == "FAILED"

    def test_a_checkout_this_stripe_account_doesnt_have_is_failed(self, db: Session, monkeypatch):
        payment, _ = _make_pending_checkout_session_payment(db, checkout_session_id="cs_test_mode_only")
        _age(db, payment, minutes=90)

        def missing(**_kw):
            raise stripe.InvalidRequestError("No such checkout.session", "id", code="resource_missing")

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "retrieve_checkout_session", missing)

        assert lf_crud.reconcile_pending_checkouts(db) == 1
        db.refresh(payment)
        assert payment.status == "FAILED"


    def test_an_unsent_refund_is_not_resent_after_a_dashboard_refund_we_havent_recorded(self, db: Session, monkeypatch):
        payment, admin = _refundable(db, "rf_dash_unrec")
        half = round(float(payment.amount) / 2, 2)
        refund = ListingFeeRefund(
            payment_id=payment.id, amount=half, currency=payment.currency, reason="t", idempotency_key="rf-dash-unrec-1",
            requested_by_admin_id=admin.id, status="REQUESTED",
        )
        db.add(refund)
        db.commit()
        _age(db, refund, minutes=30)

        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "find_refund_by_metadata", lambda **_kw: None)
        # Support refunded the other half straight in the Stripe dashboard.
        monkeypatch.setattr(stripe_client, "retrieve_amount_refunded", lambda **_kw: round(half * 100))
        monkeypatch.setattr(stripe_client, "create_refund", lambda **_kw: pytest.fail("would refund twice"))

        assert lf_crud.reconcile_processing_refunds(db) == 1
        db.refresh(refund)
        assert refund.status == "FAILED"
