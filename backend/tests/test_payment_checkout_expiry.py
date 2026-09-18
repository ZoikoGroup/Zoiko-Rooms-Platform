"""ZR-ENG-CLR-001 Section 1, Rule 7 (10.2) / AC-08: the 30-minute active
payment checkout lock (app/services/booking_expiry.py's
is_checkout_overdue/expire_checkout_if_overdue/sweep_expired_checkouts).

Mirrors test_booking_expiry.py's own structure for the 24h acceptance clock.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.finance import Obligation
from app.models.leasing import Agreement, Offer
from app.services.booking_expiry import (
    compute_checkout_deadline,
    expire_checkout_if_overdue,
    is_checkout_overdue,
    sweep_expired_checkouts,
)
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie, deliver_all_disclosures
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible
from tests.test_room_hold_atomicity import _apply_and_send_offer, _make_listing_with_room, _make_verified_renter


def _sign_both_parties_and_reach_payment_in_progress(client, db_session: Session, offer_id: int, renter, admin_cookies: dict) -> int:
    """Accept the offer, add terms, create+send+sign (both parties) the
    agreement. Returns the agreement_id, left sitting in PAYMENT_IN_PROGRESS
    since neither obligation has been paid."""
    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={
            "monthlyRent": 500, "depositAmount": 500,
            "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6,
        },
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text

    r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
    assert r.status_code == 200, r.text

    offer = db_session.get(Offer, offer_id)
    _make_agreement_eligible(db_session, offer.listing_id)

    r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    agreement_id = r.json()["id"]

    r = client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text

    deliver_all_disclosures(client, admin_cookies, agreement_id)

    r = client.post(
        f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter),
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PAYMENT_IN_PROGRESS"
    return agreement_id


class TestComputeCheckoutDeadline:
    def test_deadline_is_configured_minutes_after_start(self):
        started_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        deadline = compute_checkout_deadline(started_at)
        assert deadline == started_at + timedelta(minutes=settings.payment_checkout_lock_minutes)


class TestIsCheckoutOverdue:
    def test_in_progress_agreement_before_deadline_is_not_overdue(self):
        agreement = Agreement(status="PAYMENT_IN_PROGRESS", payment_session_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
        assert is_checkout_overdue(agreement) is False

    def test_in_progress_agreement_past_deadline_is_overdue(self):
        agreement = Agreement(status="PAYMENT_IN_PROGRESS", payment_session_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        assert is_checkout_overdue(agreement) is True

    def test_non_in_progress_agreement_is_never_overdue(self):
        agreement = Agreement(status="SIGNED", payment_session_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        assert is_checkout_overdue(agreement) is False

    def test_in_progress_agreement_with_no_deadline_is_not_overdue(self):
        agreement = Agreement(status="PAYMENT_IN_PROGRESS", payment_session_expires_at=None)
        assert is_checkout_overdue(agreement) is False


class TestLazyCheckoutExpiryOnRead:
    def test_overdue_checkout_reverts_to_sent_when_acceptance_hold_still_valid(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="checkout-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="checkout-renter1@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        agreement_id = _sign_both_parties_and_reach_payment_in_progress(client, db_session, offer_id, renter, admin_cookies)

        # 30-minute checkout lock expired, but the offer's 24h acceptance
        # window (set when the offer was accepted, well before signing) is
        # still open -- 10.2: "return to accepted state if the commercial
        # hold is still valid".
        agreement = db_session.get(Agreement, agreement_id)
        agreement.payment_session_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        r = client.get(f"/api/leasing/agreements/{agreement_id}", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SENT"
        assert r.json()["paymentSessionExpiresAt"] is None

    def test_overdue_checkout_voids_agreement_once_acceptance_window_also_expired(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="checkout-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="checkout-renter2@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        agreement_id = _sign_both_parties_and_reach_payment_in_progress(client, db_session, offer_id, renter, admin_cookies)

        agreement = db_session.get(Agreement, agreement_id)
        agreement.payment_session_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        offer = db_session.get(Offer, offer_id)
        offer.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        r = client.get(f"/api/leasing/agreements/{agreement_id}", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "VOID"


class TestSweepExpiredCheckouts:
    def test_sweep_expires_all_overdue_checkouts(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="checkout-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="checkout-renter3@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        agreement_id = _sign_both_parties_and_reach_payment_in_progress(client, db_session, offer_id, renter, admin_cookies)

        agreement = db_session.get(Agreement, agreement_id)
        agreement.payment_session_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        expired = sweep_expired_checkouts(db_session)
        assert len(expired) == 1
        assert expired[0].id == agreement_id
        assert expired[0].status == "SENT"

    def test_sweep_endpoint_is_super_admin_only(self, client, db_session: Session):
        admin = _make_admin(db_session, email="checkout-plain-admin@test.com", role="admin")
        r = client.post("/api/leasing/agreements/expire-overdue-checkouts", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text

    def test_sweep_endpoint_reports_count(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="checkout-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="checkout-renter4@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        agreement_id = _sign_both_parties_and_reach_payment_in_progress(client, db_session, offer_id, renter, admin_cookies)

        agreement = db_session.get(Agreement, agreement_id)
        agreement.payment_session_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        r = client.post("/api/leasing/agreements/expire-overdue-checkouts", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["expiredCount"] == 1
        assert agreement_id in r.json()["expiredAgreementIds"]


class TestPaymentConfirmationReachesSigned:
    def test_paying_both_obligations_moves_agreement_to_signed(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="checkout-admin5@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="checkout-renter5@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        agreement_id = _sign_both_parties_and_reach_payment_in_progress(client, db_session, offer_id, renter, admin_cookies)

        offer = db_session.get(Offer, offer_id)
        obligations = list(db_session.query(Obligation).filter(Obligation.agreement_id == agreement_id))
        total_due = sum(float(o.amount) for o in obligations)

        r = client.post(
            "/api/finance/payments",
            json={"guestId": offer.guest_id, "amount": total_due, "currency": "INR", "idempotencyKey": f"agreement-{agreement_id}-pay"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        payment_id = r.json()["id"]

        r = client.post(
            f"/api/finance/payments/{payment_id}/confirm",
            json={"allocations": [{"obligationId": o.id, "amount": float(o.amount)} for o in obligations]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status == "SIGNED"
        assert agreement.payment_session_expires_at is None
