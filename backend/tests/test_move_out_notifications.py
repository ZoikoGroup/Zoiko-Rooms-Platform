"""ZR-26: each move-out step tells the side that acts next -- the host when
the renter gives notice and when they've moved out (with the confirm
window), the renter when the host confirms."""

from sqlalchemy.orm import Session

from app.models.notification import Notification
from tests.conftest import auth_user_cookie


def _types_for(db_session: Session, user) -> list[str]:
    return [
        n.notification_type for n in db_session.query(Notification)
        .filter(Notification.recipient_user_id == user.id, Notification.notification_type.like("occupancy.move_out%"))
        .order_by(Notification.id)
    ]


class TestMoveOutNotifications:
    def test_each_step_notifies_the_other_side(self, client, db_session: Session):
        from tests.test_host_move_in_confirmation import _drive_agreement_to_signed

        host, renter, host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="mo-notif-host@test.com", renter_email="mo-notif-renter@test.com",
        )
        assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/move-out/notice", json={"notes": "End of term"}, cookies=renter_cookies,
        )
        assert r.status_code == 200, r.text
        assert _types_for(db_session, host) == ["occupancy.move_out_notice_for_host"]

        # The renter's rental shows the step and the host's confirm window.
        [rental] = client.get("/api/users/rentals/occupancies", cookies=renter_cookies).json()
        assert "MOVE_OUT_NOTICE_GIVEN" in rental["handoverEventTypes"]
        assert rental["moveOutConfirmDays"] == 7

        r = client.post(f"/api/users/rentals/occupancies/{occupancy_id}/move-out/ready", json={"notes": ""}, cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert _types_for(db_session, host) == ["occupancy.move_out_notice_for_host", "occupancy.move_out_ready_for_host"]
        ready = db_session.query(Notification).filter(
            Notification.recipient_user_id == host.id, Notification.notification_type == "occupancy.move_out_ready_for_host",
        ).one()
        assert "7 days" in ready.message

        # The host's Rental Records now offers the confirm step.
        records = client.get("/api/users/hosting/rental-records", cookies=host_cookies).json()
        record = next(r for r in records if r.get("occupancyId") == occupancy_id)
        assert record["notice"]["status"] == "RENTER_VACATED"

        r = client.post(f"/api/users/hosting/occupancies/{occupancy_id}/move-out/confirm", json={"notes": ""}, cookies=host_cookies)
        assert r.status_code == 200, r.text
        assert _types_for(db_session, renter) == ["occupancy.move_out_confirmed"]

    def test_a_retried_step_does_not_notify_twice(self, client, db_session: Session):
        from tests.test_host_move_in_confirmation import _drive_agreement_to_signed

        host, _renter, host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="mo-notif-host2@test.com", renter_email="mo-notif-renter2@test.com",
        )
        assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200
        for _ in range(2):
            r = client.post(
                f"/api/users/rentals/occupancies/{occupancy_id}/move-out/notice", json={"notes": "x"}, cookies=renter_cookies,
            )
            assert r.status_code == 200, r.text
        assert _types_for(db_session, host) == ["occupancy.move_out_notice_for_host"]

    def test_hourly_catch_up_tells_hosts_about_move_outs_started_before_the_fix(self, client, db_session: Session):
        from app.models.occupancy_activation import OccupancyHandoverEvent
        from app.services.scheduled_jobs import notify_pending_move_outs
        from tests.test_host_move_in_confirmation import _drive_agreement_to_signed

        host, renter, host_cookies, _rc, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="mo-notif-host3@test.com", renter_email="mo-notif-renter3@test.com",
        )
        assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200
        # A notice recorded the old way -- no notification went out.
        db_session.add(OccupancyHandoverEvent(
            occupancy_id=occupancy_id, event_type="MOVE_OUT_NOTICE_GIVEN", actor_kind="renter_user", actor_user_id=renter.id,
        ))
        db_session.commit()
        assert _types_for(db_session, host) == []

        assert notify_pending_move_outs(db_session) == 1
        assert _types_for(db_session, host) == ["occupancy.move_out_notice_for_host"]
        # Idempotent: the next hourly run sends nothing more.
        assert notify_pending_move_outs(db_session) == 0
        assert _types_for(db_session, host) == ["occupancy.move_out_notice_for_host"]
