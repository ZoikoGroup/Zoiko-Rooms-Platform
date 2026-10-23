"""ZR-19: the admin Settings profile rejects a malformed phone number."""

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie


def _put(client, admin, phone: str):
    return client.put(
        "/api/settings/profile",
        json={"fullName": "Zoiko Admin", "email": admin.email, "phone": phone},
        cookies=auth_admin_cookie(admin),
    )


class TestAdminProfilePhone:
    def test_valid_and_empty_phone_are_saved(self, client, db_session: Session):
        admin = _make_admin(db_session, email="phone-ok@test.com", role="super_admin")
        db_session.commit()
        r = _put(client, admin, "+91 9820011223")
        assert r.status_code == 200, r.text
        assert r.json()["phone"] == "+91 9820011223"
        assert _put(client, admin, "").status_code == 200

    def test_malformed_phone_is_rejected(self, client, db_session: Session):
        admin = _make_admin(db_session, email="phone-bad@test.com", role="super_admin")
        db_session.commit()
        for bad in ("abc", "9820011223", "+91 12", "+91 98200abc23", "+91 1234567890123456"):
            assert _put(client, admin, bad).status_code == 422, bad
