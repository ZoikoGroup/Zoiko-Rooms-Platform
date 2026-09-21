"""Section 9 gap: the move-OUT mirror of the existing move-in 3-step
handover handshake (HANDOVER_READY -> POSSESSION_DELIVERED -> RENTER_RECEIPT).
Previously a naturally-expiring tenancy had no renter-notice/host-verification
step at all -- the only way an ACTIVE occupancy ended was end_occupancy's
pure admin status flip. This tests the new MOVE_OUT_NOTICE_GIVEN ->
MOVE_OUT_READY -> HOST_MOVE_OUT_CONFIRMED trio (crud/occupancy.py:
record_handover_event, now reachable on an ACTIVE occupancy for these three
event types instead of only PENDING_MOVE_IN)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.occupancy import Occupancy
from app.models.occupancy_activation import OccupancyHandoverEvent
from tests.conftest import auth_user_cookie
from tests.test_booking_change_requests import _signed_agreement_active_occupancy


class TestMoveOutHandshake:
    def test_renter_gives_notice_then_confirms_ready_then_host_confirms(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="mo1", start_date=date.today() - timedelta(days=100),
        )
        from app.models.leasing import Agreement
        agreement = db_session.get(Agreement, agreement_id)
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/move-out/notice",
            json={"notes": "Leaving at end of lease"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["eventType"] == "MOVE_OUT_NOTICE_GIVEN"

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/move-out/ready",
            json={"notes": "Keys left with host"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["eventType"] == "MOVE_OUT_READY"

        r = client.post(
            f"/api/occupancy/{occupancy.id}/move-out/confirm",
            json={"notes": "Inspected, all good"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["eventType"] == "HOST_MOVE_OUT_CONFIRMED"

        events = db_session.scalars(
            select(OccupancyHandoverEvent).where(OccupancyHandoverEvent.occupancy_id == occupancy.id)
        ).all()
        assert {e.event_type for e in events} == {"MOVE_OUT_NOTICE_GIVEN", "MOVE_OUT_READY", "HOST_MOVE_OUT_CONFIRMED"}

    def test_a_different_renter_cannot_give_notice_for_someone_elses_occupancy(self, client, db_session: Session):
        from tests.conftest import _make_user

        agreement_id, _admin_cookies, _renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="mo2", start_date=date.today() - timedelta(days=100),
        )
        from app.models.leasing import Agreement
        agreement = db_session.get(Agreement, agreement_id)
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
        intruder = _make_user(db_session, email="mo2-intruder@test.com")

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/move-out/notice",
            json={}, cookies=auth_user_cookie(intruder),
        )
        assert r.status_code == 403, r.text

    def test_move_out_events_are_rejected_for_a_pending_move_in_occupancy(self, client, db_session: Session):
        from tests.test_booking_change_requests import _signed_agreement_before_move_in

        agreement_id, _admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="mo3", start_date=date.today() + timedelta(days=5),
        )
        from app.models.leasing import Agreement
        agreement = db_session.get(Agreement, agreement_id)
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
        assert occupancy.status == "PENDING_MOVE_IN"

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/move-out/notice",
            json={}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text

    def test_end_occupancy_picks_up_the_renters_own_notice_timestamp(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="mo4", start_date=date.today() - timedelta(days=100),
        )
        from app.models.leasing import Agreement
        agreement = db_session.get(Agreement, agreement_id)
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy.id}/move-out/notice",
            json={}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        notice_at = r.json()["createdAt"]

        r = client.post(
            f"/api/occupancy/{occupancy.id}/end", json={"overrideReason": "renter moved out early"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["noticeGivenAt"] == notice_at
