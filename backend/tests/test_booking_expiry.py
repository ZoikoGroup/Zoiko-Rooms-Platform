"""ZR-ENG-CLR-001 Section 1, Rule 7 (10.1) / AC-07: the Booking Service's
accepted-booking confirmation window (app/services/booking_expiry.py).

Scope: only the 24-hour acceptance-confirmation clock. The 30-minute payment
checkout lock (10.2) is deliberately not implemented -- see
booking_expiry.py's module docstring for why (no real payment checkout
session exists yet in this codebase to attach it to).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.leasing import Offer
from app.models.room_hold import RoomHold
from app.services.booking_expiry import (
    compute_confirmation_deadline,
    expire_offer_if_overdue,
    is_offer_overdue,
    sweep_expired_offers,
)
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_room_hold_atomicity import _apply_and_send_offer, _make_listing_with_room, _make_verified_renter


class TestComputeConfirmationDeadline:
    def test_deadline_is_configured_hours_after_acceptance(self):
        accepted_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        deadline = compute_confirmation_deadline(accepted_at)
        assert deadline == accepted_at + timedelta(hours=settings.offer_acceptance_confirmation_hours)


class TestIsOfferOverdue:
    def test_accepted_offer_before_deadline_is_not_overdue(self):
        offer = Offer(status="ACCEPTED", confirmation_expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        assert is_offer_overdue(offer) is False

    def test_accepted_offer_past_deadline_is_overdue(self):
        offer = Offer(status="ACCEPTED", confirmation_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        assert is_offer_overdue(offer) is True

    def test_non_accepted_offer_is_never_overdue(self):
        offer = Offer(status="EXPIRED", confirmation_expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        assert is_offer_overdue(offer) is False

    def test_accepted_offer_with_no_deadline_is_not_overdue(self):
        offer = Offer(status="ACCEPTED", confirmation_expires_at=None)
        assert is_offer_overdue(offer) is False


class TestAcceptingOfferStartsTheClock:
    def test_accept_stamps_accepted_at_and_confirmation_expires_at(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="expiry-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="expiry-renter1@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        before = datetime.now(timezone.utc)
        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        body = r.json()
        assert body["acceptedAt"] is not None
        assert body["confirmationExpiresAt"] is not None

        offer = db_session.get(Offer, offer_id)
        expected = compute_confirmation_deadline(offer.accepted_at)
        assert offer.accepted_at >= before
        assert offer.confirmation_expires_at == expected


class TestLazyExpiryOnRead:
    def test_overdue_offer_expires_and_releases_hold_when_read(self, client, db_session: Session):
        listing_id, room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="expiry-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="expiry-renter2@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        # Simulate the 24h window having already passed.
        offer = db_session.get(Offer, offer_id)
        offer.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        # Any admin read of the offer (e.g. via the agreement-creation route,
        # which fetches it through get_offer_or_404) self-heals it.
        r = client.get(f"/api/users/rentals/applications/{_app_id}/offer", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "EXPIRED"

        db_session.expire_all()
        active_holds = db_session.query(RoomHold).filter(
            RoomHold.room_id == room_id, RoomHold.released_at.is_(None)
        ).all()
        assert active_holds == []

    def test_expired_offer_cannot_proceed_to_agreement(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="expiry-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="expiry-renter3@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))

        offer = db_session.get(Offer, offer_id)
        offer.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_room_is_freed_for_a_new_offer_once_expired(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="expiry-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter_a = _make_verified_renter(db_session, email="expiry-renter-a@test.com")
        renter_b = _make_verified_renter(db_session, email="expiry-renter-b@test.com")

        _app_a, offer_a = _apply_and_send_offer(client, db_session, renter_a, listing_id, admin_cookies)
        client.post(f"/api/users/rentals/offers/{offer_a}/accept", cookies=auth_user_cookie(renter_a))

        offer_a_row = db_session.get(Offer, offer_a)
        offer_a_row.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        # Reading offer A anywhere lazily expires it and releases its hold --
        # only then does the room become available again for a fresh
        # application/offer/accept cycle.
        r = client.get(f"/api/users/rentals/applications/{_app_a}/offer", cookies=auth_user_cookie(renter_a))
        assert r.json()["status"] == "EXPIRED"

        _app_b, offer_b = _apply_and_send_offer(client, db_session, renter_b, listing_id, admin_cookies)
        r = client.post(f"/api/users/rentals/offers/{offer_b}/accept", cookies=auth_user_cookie(renter_b))
        assert r.status_code == 200, r.text


class TestSweepExpiredOffers:
    def test_sweep_expires_all_overdue_offers_and_releases_their_holds(self, client, db_session: Session):
        listing_id, room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="expiry-admin5@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="expiry-renter5@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))

        offer = db_session.get(Offer, offer_id)
        offer.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        expired = sweep_expired_offers(db_session)
        assert len(expired) == 1
        assert expired[0].id == offer_id
        assert expired[0].status == "EXPIRED"

        db_session.expire_all()
        active_holds = db_session.query(RoomHold).filter(
            RoomHold.room_id == room_id, RoomHold.released_at.is_(None)
        ).all()
        assert active_holds == []

    def test_sweep_endpoint_is_super_admin_only(self, client, db_session: Session):
        admin = _make_admin(db_session, email="expiry-plain-admin@test.com", role="admin")
        r = client.post("/api/leasing/offers/expire-overdue", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text

    def test_sweep_endpoint_reports_count(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="expiry-admin6@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="expiry-renter6@test.com")

        _app_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)
        client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        offer = db_session.get(Offer, offer_id)
        offer.confirmation_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()

        r = client.post("/api/leasing/offers/expire-overdue", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["expiredCount"] == 1
        assert offer_id in r.json()["expiredOfferIds"]
