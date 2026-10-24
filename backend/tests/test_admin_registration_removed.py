"""Tests for UAT batch #2, item 4: normal/public users must not be able to
self-register an Admin account. Admin accounts are provisioned only by a
super admin via POST /api/admin-users.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie


class TestPublicAdminRegistrationRemoved:
    def test_public_admin_register_endpoint_is_gone(self, client):
        r = client.post(
            "/api/auth/register",
            json={"email": "sneaky@test.com", "password": "password123", "fullName": "Sneaky", "phone": ""},
        )
        assert r.status_code == 404, r.text

    def test_creating_admin_users_requires_super_admin(self, client, db_session: Session):
        # No auth at all.
        r = client.post(
            "/api/admin-users",
            json={"email": "new-admin@test.com", "password": "password123", "fullName": "New Admin", "role": "admin"},
        )
        assert r.status_code == 401, r.text

        # A plain admin (not super_admin) is also rejected.
        plain_admin = _make_admin(db_session, email="plainadmin@test.com", role="admin")
        db_session.commit()
        r2 = client.post(
            "/api/admin-users",
            json={"email": "new-admin2@test.com", "password": "password123", "fullName": "New Admin 2", "role": "admin"},
            cookies=auth_admin_cookie(plain_admin),
        )
        assert r2.status_code == 403, r2.text

    def test_super_admin_can_still_create_admin_accounts(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="superadmin@test.com", role="super_admin")
        db_session.commit()

        r = client.post(
            "/api/admin-users",
            json={"email": "provisioned-admin@test.com", "password": "password123", "fullName": "Provisioned Admin"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["email"] == "provisioned-admin@test.com"
        assert r.json()["role"] == "super_admin"

    def test_plain_admin_role_can_no_longer_be_issued(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="superadmin-norole@test.com", role="super_admin")
        db_session.commit()
        cookies = auth_admin_cookie(super_admin)

        r = client.post(
            "/api/admin-users",
            json={"email": "plain-role@test.com", "password": "password123", "fullName": "Plain", "role": "admin"},
            cookies=cookies,
        )
        assert r.status_code == 400, r.text

        other = _make_admin(db_session, email="superadmin-other@test.com", role="super_admin")
        db_session.commit()
        r2 = client.put(f"/api/admin-users/{other.id}", json={"role": "admin"}, cookies=cookies)
        assert r2.status_code == 400, r2.text

    def test_admin_login_still_works(self, client, db_session: Session):
        admin = _make_admin(db_session, email="loginadmin@test.com", role="admin")
        db_session.commit()

        r = client.post("/api/auth/login", json={"email": "loginadmin@test.com", "password": "password123"})
        assert r.status_code == 200, r.text
        assert r.json()["email"] == "loginadmin@test.com"
