"""Coverage for the team-management notifications added to
crud/admin_user.py -- creating a team member, changing their role, and
(de)activating their account are all real events with a clear recipient
(the affected admin, plus other super admins for a new hire) that had no
notification coverage before."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from tests.conftest import _make_admin, auth_admin_cookie


class TestAdminCreationNotifies:
    def test_new_admin_and_other_super_admins_are_notified(self, client, db_session: Session):
        acting = _make_admin(db_session, email="inviter@test.com", role="super_admin")
        other_super = _make_admin(db_session, email="other-super@test.com", role="super_admin")
        db_session.commit()

        resp = client.post(
            "/api/admin-users",
            json={"email": "newbie@test.com", "password": "password123", "fullName": "Newbie", "phone": "", "role": "admin"},
            cookies=auth_admin_cookie(acting),
        )
        assert resp.status_code == 201, resp.text
        new_admin_id = resp.json()["id"]

        new_admin_notifs = notif_crud.list_for_admin(db_session, new_admin_id)
        assert any(n.notification_type == "admin_user.created" for n in new_admin_notifs)

        other_super_notifs = notif_crud.list_for_admin(db_session, other_super.id)
        assert any(n.notification_type == "admin_user.created" for n in other_super_notifs)

        # The acting super admin doesn't notify themself.
        acting_notifs = notif_crud.list_for_admin(db_session, acting.id)
        assert not any(n.notification_type == "admin_user.created" for n in acting_notifs)


class TestAdminUpdateNotifies:
    def test_role_change_notifies_target(self, client, db_session: Session):
        acting = _make_admin(db_session, email="promoter@test.com", role="super_admin")
        target = _make_admin(db_session, email="target1@test.com", role="admin")
        db_session.commit()

        resp = client.put(
            f"/api/admin-users/{target.id}",
            json={"role": "super_admin"},
            cookies=auth_admin_cookie(acting),
        )
        assert resp.status_code == 200, resp.text

        notifs = notif_crud.list_for_admin(db_session, target.id)
        assert any(n.notification_type == "admin_user.role_changed" for n in notifs)

    def test_deactivation_notifies_target(self, client, db_session: Session):
        acting = _make_admin(db_session, email="deactivator@test.com", role="super_admin")
        target = _make_admin(db_session, email="target2@test.com", role="admin")
        db_session.commit()

        resp = client.put(
            f"/api/admin-users/{target.id}",
            json={"isActive": False},
            cookies=auth_admin_cookie(acting),
        )
        assert resp.status_code == 200, resp.text

        notifs = notif_crud.list_for_admin(db_session, target.id)
        assert any(n.notification_type == "admin_user.deactivated" for n in notifs)

    def test_unrelated_field_change_does_not_notify(self, client, db_session: Session):
        acting = _make_admin(db_session, email="editor@test.com", role="super_admin")
        target = _make_admin(db_session, email="target3@test.com", role="admin")
        db_session.commit()

        resp = client.put(
            f"/api/admin-users/{target.id}",
            json={"fullName": "Renamed Person"},
            cookies=auth_admin_cookie(acting),
        )
        assert resp.status_code == 200, resp.text

        notifs = notif_crud.list_for_admin(db_session, target.id)
        assert not any(n.notification_type in ("admin_user.role_changed", "admin_user.deactivated") for n in notifs)
