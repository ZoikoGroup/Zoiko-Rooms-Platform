"""Section 11 gap: notification preferences (category opt-out + quiet
hours) -- previously no recipient had any way to shape which notifications
they receive; every notify_* call unconditionally created a row."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notification_crud
from app.models.notification import Notification
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


class TestCategoryOptOut:
    def test_opting_out_of_a_category_suppresses_that_notification(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-optout@test.com")

        r = client.put(
            "/api/users/notifications/preferences",
            json={"optedOutCategories": ["OCCUPANCY"], "quietHoursEnabled": False},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["optedOutCategories"] == ["OCCUPANCY"]

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Move-in confirmed", message="x",
            notification_type="occupancy.move_in_confirmed",
        )
        assert notification is None
        assert db_session.scalar(select(Notification).where(Notification.recipient_user_id == renter.id)) is None

    def test_a_different_category_still_delivers(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-othercat@test.com")
        client.put(
            "/api/users/notifications/preferences",
            json={"optedOutCategories": ["OCCUPANCY"], "quietHoursEnabled": False},
            cookies=auth_user_cookie(renter),
        )

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Payment confirmed", message="x", notification_type="payment.confirmed",
        )
        assert notification is not None
        assert notification.category == "PAYMENTS"

    def test_disputes_and_safety_cannot_be_opted_out(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-safety@test.com")
        r = client.put(
            "/api/users/notifications/preferences",
            json={"optedOutCategories": ["DISPUTES_AND_SAFETY", "PAYMENTS"], "quietHoursEnabled": False},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["optedOutCategories"] == ["PAYMENTS"]

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Habitability issue", message="x",
            notification_type="habitability_incident.opened",
        )
        assert notification is not None
        assert notification.category == "DISPUTES_AND_SAFETY"


class TestQuietHours:
    def test_normal_priority_suppressed_inside_the_window(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-quiet1@test.com")
        now = datetime.now(timezone.utc)
        now_minute = now.hour * 60 + now.minute
        # A 24h-wide window guaranteed to contain "now" regardless of when this
        # test actually runs, without relying on wall-clock timing tricks.
        start = (now_minute - 60) % 1440
        end = (now_minute + 60) % 1440

        r = client.put(
            "/api/users/notifications/preferences",
            json={
                "optedOutCategories": [], "quietHoursEnabled": True,
                "quietHoursStartMinute": start, "quietHoursEndMinute": end,
            },
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Payment confirmed", message="x", notification_type="payment.confirmed",
        )
        assert notification is None

    def test_high_priority_bypasses_quiet_hours(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-quiet2@test.com")
        now = datetime.now(timezone.utc)
        now_minute = now.hour * 60 + now.minute
        start = (now_minute - 60) % 1440
        end = (now_minute + 60) % 1440

        client.put(
            "/api/users/notifications/preferences",
            json={
                "optedOutCategories": [], "quietHoursEnabled": True,
                "quietHoursStartMinute": start, "quietHoursEndMinute": end,
            },
            cookies=auth_user_cookie(renter),
        )

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Habitability issue", message="x",
            notification_type="habitability_incident.opened",
        )
        assert notification is not None
        assert notification.priority == "HIGH"

    def test_disabled_quiet_hours_never_suppress(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-quiet3@test.com")
        client.put(
            "/api/users/notifications/preferences",
            json={"optedOutCategories": [], "quietHoursEnabled": False, "quietHoursStartMinute": 0, "quietHoursEndMinute": 1439},
            cookies=auth_user_cookie(renter),
        )

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Payment confirmed", message="x", notification_type="payment.confirmed",
        )
        assert notification is not None


class TestPreferencesDefaultsAndAdmin:
    def test_a_user_with_no_preference_row_gets_defaults_and_all_notifications_deliver(self, client, db_session: Session):
        renter = _make_user(db_session, email="notif-pref-defaults@test.com")
        r = client.get("/api/users/notifications/preferences", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["optedOutCategories"] == []
        assert body["quietHoursEnabled"] is False

        notification = notification_crud.notify_user(
            db_session, renter.id, title="Payment confirmed", message="x", notification_type="payment.confirmed",
        )
        assert notification is not None

    def test_admin_preferences_round_trip(self, client, db_session: Session):
        admin = _make_admin(db_session, email="notif-pref-admin@test.com", role="super_admin")
        r = client.put(
            "/api/notifications/preferences",
            json={"optedOutCategories": ["LEASING"], "quietHoursEnabled": False},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["optedOutCategories"] == ["LEASING"]

        notification = notification_crud.notify_admin(
            db_session, admin.id, title="New listing", message="x", notification_type="listing.submitted",
        )
        assert notification is None
