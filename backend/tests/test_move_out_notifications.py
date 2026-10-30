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


class TestEarlyEndNotice:
    def test_host_is_told_even_when_the_listing_has_no_party_id(self, client, db_session: Session):
        """Admin-created listings leave Listing.party_id empty; the host is the
        property's owning party, so the early-end notice must still reach them."""
        from app.models.occupancy import Occupancy
        from tests.test_host_move_in_confirmation import _drive_agreement_to_signed

        host, _renter, host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="mo-early-host@test.com", renter_email="mo-early-renter@test.com",
        )
        assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200
        db_session.get(Occupancy, occupancy_id).listing.party_id = None
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT", "notes": "new job"}, cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        told = db_session.query(Notification).filter(
            Notification.recipient_user_id == host.id, Notification.notification_type == "termination_case.opened",
        ).all()
        assert len(told) == 1

    def _open_early_end(self, client, db_session: Session, tag: str):
        from tests.test_host_move_in_confirmation import _drive_agreement_to_signed

        host, renter, host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email=f"mo-{tag}-host@test.com", renter_email=f"mo-{tag}-renter@test.com",
        )
        assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200
        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT", "notes": "new job"}, cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        return host, renter, host_cookies, renter_cookies, occupancy_id, r.json()["id"]

    def test_host_acknowledging_the_notice_tells_the_renter(self, client, db_session: Session):
        host, renter, host_cookies, _rc, occupancy_id, case_id = self._open_early_end(client, db_session, "ack")

        records = client.get("/api/users/hosting/rental-records", cookies=host_cookies).json()
        record = next(r for r in records if r.get("occupancyId") == occupancy_id)
        assert record["notice"]["acknowledgedAt"] is None

        r = client.post(f"/api/users/hosting/termination-cases/{case_id}/acknowledge", cookies=host_cookies)
        assert r.status_code == 200, r.text
        records = client.get("/api/users/hosting/rental-records", cookies=host_cookies).json()
        record = next(r for r in records if r.get("occupancyId") == occupancy_id)
        assert record["notice"]["acknowledgedAt"] is not None
        told = db_session.query(Notification).filter(
            Notification.recipient_user_id == renter.id, Notification.notification_type == "termination_case.notice_acknowledged",
        ).all()
        assert len(told) == 1

        # A second tap doesn't tell them again.
        assert client.post(f"/api/users/hosting/termination-cases/{case_id}/acknowledge", cookies=host_cookies).status_code == 200
        assert db_session.query(Notification).filter(
            Notification.recipient_user_id == renter.id, Notification.notification_type == "termination_case.notice_acknowledged",
        ).count() == 1

    def test_everyone_on_the_hosts_party_is_told(self, client, db_session: Session):
        from tests.conftest import _make_user
        from tests.test_host_move_in_confirmation import _drive_agreement_to_signed

        host, _renter, host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
            client, db_session, host_email="mo-team-host@test.com", renter_email="mo-team-renter@test.com",
        )
        assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200
        manager = _make_user(db_session, email="mo-team-manager@test.com")
        manager.party_id = host.party_id
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/occupancies/{occupancy_id}/termination-cases",
            json={"causeCode": "RENTER_ORDINARY_EARLY_EXIT", "notes": "x"}, cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        for person in (host, manager):
            assert db_session.query(Notification).filter(
                Notification.recipient_user_id == person.id, Notification.notification_type == "termination_case.opened",
            ).count() == 1

    def test_a_case_left_in_admin_review_goes_to_host_and_renter(self, client, db_session: Session):
        from app.crud.termination_responses import route_pending_reviews_to_parties
        from app.models.termination_case import TerminationCase

        host, _renter, _hc, _rc, _occupancy_id, case_id = self._open_early_end(client, db_session, "legacy")
        case = db_session.get(TerminationCase, case_id)
        case.status, case.earliest_effective_date, case.effective_termination_date = "PENDING_REVIEW", None, None
        db_session.commit()

        assert route_pending_reviews_to_parties(db_session) == 1
        db_session.refresh(case)
        assert case.status == "AWAITING_RESPONSE" and case.earliest_effective_date is not None
        assert case.proposer == str(case.initiator_guest_id) or case.proposer == case.initiator_guest_id
        assert route_pending_reviews_to_parties(db_session) == 0
