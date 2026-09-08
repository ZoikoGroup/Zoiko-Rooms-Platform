"""Coverage for the notification inbox routes (routes/admin_notifications.py,
routes/user_notifications.py) -- list/unread-count/mark-read/mark-all-read,
and that each recipient can only ever see their own rows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


class TestUserNotifications:
    def test_list_and_unread_count(self, client, db_session: Session):
        user = _make_user(db_session, email="notif-user@test.com")
        db_session.flush()
        notif_crud.notify_user(
            db_session, user.id, title="Rent due", message="Your rent is due soon.",
            notification_type="occupancy.rent_due",
        )
        db_session.commit()

        list_resp = client.get("/api/users/notifications", cookies=auth_user_cookie(user))
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["title"] == "Rent due"

        count_resp = client.get("/api/users/notifications/unread-count", cookies=auth_user_cookie(user))
        assert count_resp.json()["count"] == 1

    def test_mark_read_and_mark_all_read(self, client, db_session: Session):
        user = _make_user(db_session, email="notif-user2@test.com")
        db_session.flush()
        # Distinct related_entity_id values -- notify_user's dedup key is
        # (recipient, type, entity), so identical calls would silently collapse
        # into a single row (see crud/notification.py::_create's docstring).
        n1 = notif_crud.notify_user(
            db_session, user.id, title="A", message="a", notification_type="t",
            related_entity_type="x", related_entity_id="1",
        )
        notif_crud.notify_user(
            db_session, user.id, title="B", message="b", notification_type="t",
            related_entity_type="x", related_entity_id="2",
        )
        db_session.commit()

        read_resp = client.patch(f"/api/users/notifications/{n1.id}/read", cookies=auth_user_cookie(user))
        assert read_resp.status_code == 200
        assert read_resp.json()["isRead"] is True

        count_after_one = client.get("/api/users/notifications/unread-count", cookies=auth_user_cookie(user)).json()
        assert count_after_one["count"] == 1

        all_read_resp = client.patch("/api/users/notifications/read-all", cookies=auth_user_cookie(user))
        assert all_read_resp.status_code == 200
        assert all_read_resp.json()["updated"] == 1

        count_after_all = client.get("/api/users/notifications/unread-count", cookies=auth_user_cookie(user)).json()
        assert count_after_all["count"] == 0

    def test_cannot_see_another_users_notifications(self, client, db_session: Session):
        owner = _make_user(db_session, email="notif-owner@test.com")
        stranger = _make_user(db_session, email="notif-stranger@test.com")
        db_session.flush()
        notif_crud.notify_user(db_session, owner.id, title="Private", message="p", notification_type="t")
        db_session.commit()

        resp = client.get("/api/users/notifications", cookies=auth_user_cookie(stranger))
        assert resp.json() == []

    def test_cannot_mark_another_users_notification_read(self, client, db_session: Session):
        owner = _make_user(db_session, email="notif-owner2@test.com")
        stranger = _make_user(db_session, email="notif-stranger2@test.com")
        db_session.flush()
        n = notif_crud.notify_user(db_session, owner.id, title="Private", message="p", notification_type="t")
        db_session.commit()

        resp = client.patch(f"/api/users/notifications/{n.id}/read", cookies=auth_user_cookie(stranger))
        assert resp.status_code == 404


class TestAdminNotifications:
    def test_list_and_unread_count(self, client, db_session: Session):
        admin = _make_admin(db_session, email="notif-admin@test.com")
        db_session.flush()
        notif_crud.notify_admin(
            db_session, admin.id, title="New application", message="A renter applied.", notification_type="application.received",
        )
        db_session.commit()

        list_resp = client.get("/api/notifications", cookies=auth_admin_cookie(admin))
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        count_resp = client.get("/api/notifications/unread-count", cookies=auth_admin_cookie(admin))
        assert count_resp.json()["count"] == 1

    def test_cannot_see_another_admins_notifications(self, client, db_session: Session):
        owner = _make_admin(db_session, email="notif-admin-owner@test.com")
        other = _make_admin(db_session, email="notif-admin-other@test.com")
        db_session.flush()
        notif_crud.notify_admin(db_session, owner.id, title="Private", message="p", notification_type="t")
        db_session.commit()

        resp = client.get("/api/notifications", cookies=auth_admin_cookie(other))
        assert resp.json() == []

    def test_requires_auth(self, client):
        assert client.get("/api/notifications").status_code == 401
        assert client.get("/api/users/notifications").status_code == 401
