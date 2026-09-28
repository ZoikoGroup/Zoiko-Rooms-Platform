"""Rent is strictly between tenant and host -- Zoiko Rooms never holds it,
pays its fees, or absorbs its losses. Covers the money paths that rule
depends on:
- a host's rent account carries its own Stripe fees and chargebacks
  (services/stripe_client.py:create_connected_account);
- card-paid rent/deposit is refunded from the host's own Stripe account
  when a booking is cancelled before move-in (crud/occupancy.py:
  cancel_before_move_in), and a cancelled booking can't be paid afterwards;
- a tenant's card chargeback is mirrored onto the rent payment
  (crud/external_payment_session.py:record_provider_dispute)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import external_payment_session as eps_crud
from app.models.external_payment_session import ExternalPaymentSession
from app.models.rental_payment import RentalPaymentObligation, RentalPaymentRecord
from app.services import stripe_client
from tests.conftest import auth_user_cookie
from tests.test_external_payment_session import _make_obligation_with_charge_ready_recipient
from tests.test_pre_move_in_cancellation import _occupancy_for_agreement, _seed_system_admin
from tests.test_booking_change_requests import _signed_agreement_before_move_in


class TestRentAccountsCarryTheirOwnFeesAndLosses:
    def _capture_account_create(self, monkeypatch) -> dict:
        captured: dict = {}

        def create(*, params):
            captured.update(params)
            return SimpleNamespace(id="acct_test_created")

        fake = SimpleNamespace(v2=SimpleNamespace(core=SimpleNamespace(accounts=SimpleNamespace(create=create))))
        monkeypatch.setattr(stripe_client, "is_configured", lambda: True)
        monkeypatch.setattr(stripe_client, "stripe_client_v2", lambda: fake)
        return captured

    def test_rent_merchant_account_puts_fees_and_losses_on_the_host(self, monkeypatch):
        captured = self._capture_account_create(monkeypatch)
        stripe_client.create_connected_account(country="GB", email="host@test.com", metadata={}, configuration="merchant")
        assert captured["dashboard"] == "full"
        assert captured["defaults"]["responsibilities"] == {"fees_collector": "stripe", "losses_collector": "stripe"}

    def test_legacy_payout_recipient_account_is_unchanged(self, monkeypatch):
        captured = self._capture_account_create(monkeypatch)
        stripe_client.create_connected_account(country="GB", email="host@test.com", metadata={}, configuration="recipient")
        assert captured["dashboard"] == "express"
        assert captured["defaults"]["responsibilities"] == {"fees_collector": "application", "losses_collector": "application"}


def _rental_obligation(db: Session, agreement_id: int, obligation_type: str) -> RentalPaymentObligation:
    obligation = db.scalar(select(RentalPaymentObligation).where(
        RentalPaymentObligation.agreement_id == agreement_id, RentalPaymentObligation.obligation_type == obligation_type,
    ))
    assert obligation is not None, "signing should have created the online-rail obligation"
    return obligation


def _card_pay(db: Session, agreement_id: int, obligation_type: str, *, intent_id: str) -> RentalPaymentRecord:
    """A rent/deposit paid by card through Stripe Checkout -- money in the
    host's own connected account. Built directly (the Stripe-hosted page
    can't run in tests) in exactly the shape record_provider_payment_success
    leaves behind."""
    obligation = _rental_obligation(db, agreement_id, obligation_type)
    db.add(ExternalPaymentSession(
        obligation_id=obligation.id, tenant_guest_id=obligation.tenant_guest_id,
        recipient_stripe_account_id="acct_test_host", provider_checkout_session_id=f"cs_{intent_id}",
        provider_payment_intent_id=intent_id, status="SUCCEEDED", amount=obligation.amount, currency=obligation.currency,
    ))
    record = RentalPaymentRecord(
        obligation_id=obligation.id, status="CONFIRMED", provenance="PROVIDER_CONFIRMATION",
        declared_amount=obligation.amount, declared_currency=obligation.currency, declared_date=date.today(),
        payment_method_category="CARD", declared_by_guest_id=obligation.tenant_guest_id,
        confirmed_amount=obligation.amount, confirmed_at=datetime.now(timezone.utc), provider_reference=intent_id,
    )
    db.add(record)
    obligation.status = "CONFIRMED"
    db.commit()
    return record


def _capture_refunds(monkeypatch, *, fail: bool = False) -> list[dict]:
    calls: list[dict] = []

    def fake_refund(**kwargs):
        if fail:
            raise RuntimeError("Stripe refused the refund")
        calls.append(kwargs)
        return f"re_test_{len(calls)}"

    monkeypatch.setattr(stripe_client, "create_rent_payment_refund", fake_refund)
    return calls


def _cancel(client, occupancy_id: int, renter):
    return client.post(
        f"/api/users/rentals/occupancies/{occupancy_id}/cancel-before-move-in",
        json={"reason": "changed my mind"}, cookies=auth_user_cookie(renter),
    )


class TestCardPaidBookingCancellation:
    def test_card_paid_rent_is_refunded_from_the_hosts_own_stripe_account(self, client, db_session: Session, monkeypatch):
        agreement_id, _admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="rmf-card1", start_date=date.today() + timedelta(days=10),
        )
        record = _card_pay(db_session, agreement_id, "RENT", intent_id="pi_test_card_rent")
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        _seed_system_admin(db_session)
        refunds = _capture_refunds(monkeypatch)

        r = _cancel(client, occupancy.id, renter)
        assert r.status_code == 200, r.text

        assert len(refunds) == 1
        assert refunds[0]["payment_intent_id"] == "pi_test_card_rent"
        assert refunds[0]["connected_account_id"] == "acct_test_host"  # the host's account, never the platform's
        assert refunds[0]["amount"] == float(record.declared_amount)
        db_session.refresh(record)
        assert record.provider_refund_id == "re_test_1"
        assert float(record.refunded_amount) == float(record.declared_amount)
        # The fixture's own 1000 paid on the legacy rail, plus this card payment.
        assert r.json()["refundedAmount"] == round(1000.0 + float(record.declared_amount), 2)

        still_open = db_session.scalars(select(RentalPaymentObligation).where(
            RentalPaymentObligation.agreement_id == agreement_id,
            RentalPaymentObligation.status.notin_(("CANCELLED", "WAIVED")),
        )).all()
        assert still_open == []  # nothing left to pay on a cancelled booking

    def test_a_stripe_refusal_leaves_the_booking_uncancelled(self, client, db_session: Session, monkeypatch):
        agreement_id, _admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="rmf-card2", start_date=date.today() + timedelta(days=10),
        )
        record = _card_pay(db_session, agreement_id, "DEPOSIT", intent_id="pi_test_card_dep")
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        _seed_system_admin(db_session)
        _capture_refunds(monkeypatch, fail=True)

        r = _cancel(client, occupancy.id, renter)
        assert r.status_code == 502, r.text
        db_session.refresh(occupancy)
        db_session.refresh(record)
        assert occupancy.status == "PENDING_MOVE_IN"
        assert record.provider_refund_id == ""

    def test_an_open_checkout_is_closed_so_a_cancelled_booking_cant_be_paid(self, client, db_session: Session, monkeypatch):
        agreement_id, _admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="rmf-card3", start_date=date.today() + timedelta(days=10),
        )
        obligation = _rental_obligation(db_session, agreement_id, "RENT")
        session = ExternalPaymentSession(
            obligation_id=obligation.id, tenant_guest_id=obligation.tenant_guest_id,
            recipient_stripe_account_id="acct_test_host", provider_checkout_session_id="cs_test_open",
            status="STARTED", amount=obligation.amount, currency=obligation.currency,
        )
        db_session.add(session)
        db_session.commit()
        expired: list[tuple[str, str]] = []
        monkeypatch.setattr(stripe_client, "retrieve_rent_payment_checkout_session", lambda **_kw: {
            "payment_status": "unpaid", "payment_intent_id": None, "status": "open", "url": "https://checkout.test",
        })
        monkeypatch.setattr(
            stripe_client, "expire_rent_payment_checkout_session",
            lambda *, checkout_session_id, connected_account_id: expired.append((checkout_session_id, connected_account_id)),
        )
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        _seed_system_admin(db_session)

        r = _cancel(client, occupancy.id, renter)
        assert r.status_code == 200, r.text
        assert expired == [("cs_test_open", "acct_test_host")]
        db_session.refresh(session)
        db_session.refresh(obligation)
        assert session.status == "FAILED"
        assert obligation.status == "CANCELLED"


def _dispute_event(event_id: str, account_id: str, payment_intent_id: str, *, event_type: str, dispute_status: str) -> dict:
    return {
        "id": event_id, "type": event_type, "account": account_id,
        "data": {"object": {"id": "dp_test_1", "payment_intent": payment_intent_id, "status": dispute_status}},
    }


class TestChargebacks:
    def _paid(self, db: Session, guest_id: str):
        obligation, tenant, _recipient, account = _make_obligation_with_charge_ready_recipient(db, guest_id=guest_id)
        session, _url = eps_crud.create_session(  # simulated path -- CONFIRMED immediately
            db, tenant, obligation, success_url="https://app.test/return", cancel_url="https://app.test/return",
        )
        return obligation, account, session

    def _send(self, db: Session, account_id: str, session, *events: tuple[str, str, str]) -> None:
        for event_id, event_type, dispute_status in events:
            eps_crud.ingest_stripe_webhook_event(db, _dispute_event(
                event_id, account_id, session.provider_payment_intent_id,
                event_type=event_type, dispute_status=dispute_status,
            ))

    def test_an_opened_chargeback_marks_the_payment_disputed(self, db_session: Session):
        obligation, account, session = self._paid(db_session, "G-RMF-CB1")
        self._send(db_session, account.stripe_account_id, session, ("evt_cb_open_1", "charge.dispute.created", "needs_response"))
        record = db_session.query(RentalPaymentRecord).filter_by(obligation_id=obligation.id).one()
        assert record.status == "DISPUTED"
        assert record.provider_dispute_id == "dp_test_1"
        db_session.refresh(obligation)
        assert obligation.status == "DISPUTED"

    def test_a_lost_chargeback_makes_the_rent_due_again(self, db_session: Session):
        obligation, account, session = self._paid(db_session, "G-RMF-CB2")
        self._send(
            db_session, account.stripe_account_id, session,
            ("evt_cb_open_2", "charge.dispute.created", "needs_response"),
            ("evt_cb_close_2", "charge.dispute.closed", "lost"),
        )
        db_session.refresh(obligation)
        assert obligation.status == "REVERSED"

    def test_a_won_chargeback_restores_the_payment(self, db_session: Session):
        obligation, account, session = self._paid(db_session, "G-RMF-CB3")
        self._send(
            db_session, account.stripe_account_id, session,
            ("evt_cb_open_3", "charge.dispute.created", "needs_response"),
            ("evt_cb_close_3", "charge.dispute.closed", "won"),
        )
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED"

    def test_a_chargeback_reported_for_a_different_account_is_ignored(self, db_session: Session):
        obligation, account, session = self._paid(db_session, "G-RMF-CB4")
        # A known connected account (so the event isn't dropped at the door),
        # but not the one this payment was made to.
        _other, _t, _r, other_account = _make_obligation_with_charge_ready_recipient(db_session, guest_id="G-RMF-CB4-OTHER")
        self._send(db_session, other_account.stripe_account_id, session, ("evt_cb_other_4", "charge.dispute.created", "needs_response"))
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED"


def _refund_event(event_id: str, account_id: str, payment_intent_id: str, *, amount_refunded_minor: int) -> dict:
    return {
        "id": event_id, "type": "charge.refunded", "account": account_id,
        "data": {"object": {
            "id": f"ch_{payment_intent_id}", "object": "charge", "payment_intent": payment_intent_id,
            "amount_refunded": amount_refunded_minor, "currency": "gbp",
        }},
    }


class TestLostChargebackUndoesMoveInEligibility:
    def test_a_lost_deposit_chargeback_puts_the_legacy_deposit_back_to_unpaid(self, client, db_session: Session):
        from app.crud.rental_payment import recompute_obligation_status
        from app.models.finance import Obligation as LegacyObligation, PaymentAllocation

        agreement_id, _admin_cookies, _renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="rmf-unsync", start_date=date.today() + timedelta(days=10),
        )
        legacy_deposit = db_session.scalar(select(LegacyObligation).where(
            LegacyObligation.agreement_id == agreement_id, LegacyObligation.obligation_type == "DEPOSIT",
        ).order_by(LegacyObligation.id))
        # Make it look like the card rail paid it: PAID by the status sync
        # alone, with no legacy-rail allocation behind it.
        for allocation in db_session.scalars(select(PaymentAllocation).where(PaymentAllocation.obligation_id == legacy_deposit.id)):
            db_session.delete(allocation)
        db_session.commit()
        record = _card_pay(db_session, agreement_id, "DEPOSIT", intent_id="pi_unsync_dep")
        recompute_obligation_status(db_session, record.obligation)
        db_session.commit()
        db_session.refresh(legacy_deposit)
        assert legacy_deposit.status == "PAID"

        for event_type, dispute_status in (("CHARGE_DISPUTE_CREATED", "needs_response"), ("CHARGE_DISPUTE_CLOSED", "lost")):
            eps_crud.record_provider_dispute(
                db_session, {"id": "dp_unsync", "payment_intent": "pi_unsync_dep", "status": dispute_status},
                event_type=event_type, connected_account_id="acct_test_host",
            )

        db_session.refresh(legacy_deposit)
        assert legacy_deposit.status == "PENDING"  # move-in eligibility no longer sees it as paid
        assert legacy_deposit.deposit_record is None
        db_session.refresh(record)
        assert record.status == "REVERSED"


class TestRefundsMadeOutsideZoiko:
    def _paid(self, db: Session, guest_id: str):
        return TestChargebacks()._paid(db, guest_id)

    def test_a_full_dashboard_refund_makes_the_rent_due_again(self, db_session: Session):
        obligation, account, session = self._paid(db_session, "G-RMF-RF1")
        eps_crud.ingest_stripe_webhook_event(db_session, _refund_event(
            "evt_rf_full", account.stripe_account_id, session.provider_payment_intent_id, amount_refunded_minor=85000,
        ))
        record = db_session.query(RentalPaymentRecord).filter_by(obligation_id=obligation.id).one()
        assert record.status == "REVERSED"
        assert float(record.refunded_amount) == 850.0
        db_session.refresh(obligation)
        assert obligation.status == "REVERSED"
        assert obligation.outstanding_amount == 850.0

    def test_a_partial_refund_leaves_only_the_remainder_payable_online(self, db_session: Session):
        obligation, account, session = self._paid(db_session, "G-RMF-RF2")
        eps_crud.ingest_stripe_webhook_event(db_session, _refund_event(
            "evt_rf_part", account.stripe_account_id, session.provider_payment_intent_id, amount_refunded_minor=20000,
        ))
        db_session.refresh(obligation)
        assert obligation.status == "PARTIALLY_PAID"
        assert obligation.outstanding_amount == 200.0

        second, _url = eps_crud.create_session(
            db_session, obligation.tenant, obligation,
            success_url="https://app.test/return", cancel_url="https://app.test/return",
        )
        assert float(second.amount) == 200.0  # never the full 850 again
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED"
        assert obligation.outstanding_amount == 0.0

    def test_a_replayed_refund_event_changes_nothing(self, db_session: Session):
        obligation, account, session = self._paid(db_session, "G-RMF-RF3")
        event = _refund_event("evt_rf_dup", account.stripe_account_id, session.provider_payment_intent_id, amount_refunded_minor=10000)
        eps_crud.ingest_stripe_webhook_event(db_session, event)
        eps_crud.ingest_stripe_webhook_event(db_session, event)
        record = db_session.query(RentalPaymentRecord).filter_by(obligation_id=obligation.id).one()
        assert float(record.refunded_amount) == 100.0

    def test_a_failed_refund_restores_the_payment(self, db_session: Session):
        obligation, account, session = self._paid(db_session, "G-RMF-RF4")
        intent = session.provider_payment_intent_id
        eps_crud.ingest_stripe_webhook_event(db_session, _refund_event("evt_rf4_a", account.stripe_account_id, intent, amount_refunded_minor=85000))
        # Stripe lowers amount_refunded again once a refund fails.
        eps_crud.ingest_stripe_webhook_event(db_session, _refund_event("evt_rf4_b", account.stripe_account_id, intent, amount_refunded_minor=0))
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED"


class TestDisputeEdgeCases:
    def test_a_dispute_closed_as_charge_refunded_is_not_treated_as_paid(self, db_session: Session):
        chargebacks = TestChargebacks()
        obligation, account, session = chargebacks._paid(db_session, "G-RMF-DE1")
        chargebacks._send(db_session, account.stripe_account_id, session, ("evt_de1_open", "charge.dispute.created", "warning_needs_response"))
        eps_crud.ingest_stripe_webhook_event(db_session, _refund_event(
            "evt_de1_rf", account.stripe_account_id, session.provider_payment_intent_id, amount_refunded_minor=85000,
        ))
        chargebacks._send(db_session, account.stripe_account_id, session, ("evt_de1_close", "charge.dispute.closed", "charge_refunded"))
        db_session.refresh(obligation)
        assert obligation.status == "REVERSED"

    def test_a_late_update_never_reopens_a_closed_dispute(self, db_session: Session):
        chargebacks = TestChargebacks()
        obligation, account, session = chargebacks._paid(db_session, "G-RMF-DE2")
        chargebacks._send(
            db_session, account.stripe_account_id, session,
            ("evt_de2_open", "charge.dispute.created", "needs_response"),
            ("evt_de2_won", "charge.dispute.closed", "won"),
            ("evt_de2_late", "charge.dispute.updated", "under_review"),  # delivered out of order
        )
        db_session.refresh(obligation)
        assert obligation.status == "CONFIRMED"

    def _started_session(self, db: Session, guest_id: str):
        obligation, tenant, _recipient, account = _make_obligation_with_charge_ready_recipient(db, guest_id=guest_id)
        session = ExternalPaymentSession(
            obligation_id=obligation.id, tenant_guest_id=tenant.id, recipient_stripe_account_id=account.stripe_account_id,
            provider_checkout_session_id=f"cs_{guest_id}", status="STARTED", amount=obligation.amount, currency=obligation.currency,
        )
        db.add(session)
        db.commit()
        return obligation, account, session

    def test_a_dispute_that_beats_its_payment_records_the_payment_first(self, db_session: Session, monkeypatch):
        obligation, account, session = self._started_session(db_session, "G-RMF-DE3")
        monkeypatch.setattr(
            stripe_client, "find_rent_checkout_session_id_for_payment_intent", lambda **_kw: session.provider_checkout_session_id,
        )
        monkeypatch.setattr(stripe_client, "retrieve_rent_payment_checkout_session", lambda **_kw: {
            "payment_status": "paid", "payment_intent_id": "pi_de3", "status": "complete", "url": "",
        })
        eps_crud.ingest_stripe_webhook_event(db_session, _dispute_event(
            "evt_de3", account.stripe_account_id, "pi_de3", event_type="charge.dispute.created", dispute_status="needs_response",
        ))
        record = db_session.query(RentalPaymentRecord).filter_by(obligation_id=obligation.id).one()
        assert record.status == "DISPUTED"

    def test_a_dispute_whose_payment_is_not_confirmed_yet_is_left_for_stripe_to_redeliver(self, db_session: Session, monkeypatch):
        import pytest

        from app.models.external_payment_session import RentalPaymentProviderEvent

        _obligation, account, session = self._started_session(db_session, "G-RMF-DE4")
        monkeypatch.setattr(
            stripe_client, "find_rent_checkout_session_id_for_payment_intent", lambda **_kw: session.provider_checkout_session_id,
        )
        monkeypatch.setattr(stripe_client, "retrieve_rent_payment_checkout_session", lambda **_kw: {
            "payment_status": "unpaid", "payment_intent_id": None, "status": "open", "url": "https://checkout.test",
        })
        with pytest.raises(eps_crud.ProviderEventNotReady):
            eps_crud.ingest_stripe_webhook_event(db_session, _dispute_event(
                "evt_de4", account.stripe_account_id, "pi_de4", event_type="charge.dispute.created", dispute_status="needs_response",
            ))
        db_session.rollback()
        # Not marked as seen -- Stripe's redelivery will be processed.
        assert db_session.query(RentalPaymentProviderEvent).filter_by(provider_event_id="evt_de4").count() == 0


class TestCancellationRefundRetries:
    def test_a_retry_after_a_partial_failure_never_refunds_twice(self, client, db_session: Session, monkeypatch):
        agreement_id, _admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="rmf-retry", start_date=date.today() + timedelta(days=10),
        )
        deposit = _card_pay(db_session, agreement_id, "DEPOSIT", intent_id="pi_retry_dep")
        rent = _card_pay(db_session, agreement_id, "RENT", intent_id="pi_retry_rent")
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        _seed_system_admin(db_session)

        calls: list[dict] = []

        def flaky_refund(**kwargs):
            calls.append(kwargs)
            if kwargs["payment_intent_id"] == "pi_retry_rent" and len(calls) == 2:
                raise RuntimeError("Stripe timed out")
            return f"re_retry_{len(calls)}"

        monkeypatch.setattr(stripe_client, "create_rent_payment_refund", flaky_refund)
        assert _cancel(client, occupancy.id, renter).status_code == 502
        db_session.refresh(deposit)
        assert deposit.provider_refund_id == "re_retry_1"  # kept, although the cancellation itself failed

        r = _cancel(client, occupancy.id, renter)
        assert r.status_code == 200, r.text
        refunded_intents = [c["payment_intent_id"] for c in calls]
        assert refunded_intents.count("pi_retry_dep") == 1  # never refunded a second time
        assert refunded_intents.count("pi_retry_rent") == 2  # the failed attempt, then the retry
        db_session.refresh(rent)
        assert float(rent.refunded_amount) == float(rent.declared_amount)

    def test_a_card_refunded_booking_leaves_nothing_owing_on_the_legacy_side(self, client, db_session: Session, monkeypatch):
        from app.crud.rental_payment import recompute_obligation_status
        from app.models.finance import Obligation as LegacyObligation, PaymentAllocation

        agreement_id, _admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="rmf-legacy-refunded", start_date=date.today() + timedelta(days=10),
        )
        legacy_deposit = db_session.scalar(select(LegacyObligation).where(
            LegacyObligation.agreement_id == agreement_id, LegacyObligation.obligation_type == "DEPOSIT",
        ).order_by(LegacyObligation.id))
        for allocation in db_session.scalars(select(PaymentAllocation).where(PaymentAllocation.obligation_id == legacy_deposit.id)):
            db_session.delete(allocation)
        db_session.commit()
        record = _card_pay(db_session, agreement_id, "DEPOSIT", intent_id="pi_legacy_refunded")
        recompute_obligation_status(db_session, record.obligation)
        db_session.commit()
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        _seed_system_admin(db_session)
        _capture_refunds(monkeypatch)

        assert _cancel(client, occupancy.id, renter).status_code == 200
        db_session.refresh(legacy_deposit)
        assert legacy_deposit.status == "REFUNDED"

    def test_a_refund_the_host_already_gave_is_not_repeated(self, client, db_session: Session, monkeypatch):
        agreement_id, _admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="rmf-prior", start_date=date.today() + timedelta(days=10),
        )
        record = _card_pay(db_session, agreement_id, "RENT", intent_id="pi_prior_rent")
        record.refunded_amount = 100
        eps_crud.apply_provider_state(record)
        db_session.commit()
        occupancy = _occupancy_for_agreement(db_session, agreement_id)
        _seed_system_admin(db_session)
        refunds = _capture_refunds(monkeypatch)

        r = _cancel(client, occupancy.id, renter)
        assert r.status_code == 200, r.text
        assert len(refunds) == 1
        assert refunds[0]["amount"] == round(float(record.declared_amount) - 100, 2)
        db_session.refresh(record)
        assert float(record.refunded_amount) == float(record.declared_amount)
