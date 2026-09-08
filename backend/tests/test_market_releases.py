"""Coverage for routes/market_releases.py -- the compliance gate that
check_marketplace_standing (crud/eligibility.py) relies on to decide whether a
jurisdiction is even open for business. Super-admin-only throughout."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie


class TestMarketReleaseLifecycle:
    def test_create_list_approve_disable(self, client, db_session: Session):
        admin = _make_admin(db_session, email="mr-super@test.com", role="super_admin")
        db_session.commit()

        created = client.post(
            "/api/market-releases",
            json={"jurisdiction": "IN-KA", "minStayNights": 30},
            cookies=auth_admin_cookie(admin),
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["jurisdiction"] == "IN-KA"
        assert body["status"] == "draft"
        assert body["minStayNights"] == 30

        listed = client.get("/api/market-releases", cookies=auth_admin_cookie(admin))
        assert listed.status_code == 200
        assert any(r["id"] == body["id"] for r in listed.json())

        approved = client.post(f"/api/market-releases/{body['id']}/approve", cookies=auth_admin_cookie(admin))
        assert approved.status_code == 200
        assert approved.json()["status"] == "active"

        disabled = client.post(f"/api/market-releases/{body['id']}/disable", cookies=auth_admin_cookie(admin))
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"

    def test_approve_missing_release_is_404(self, client, db_session: Session):
        admin = _make_admin(db_session, email="mr-super2@test.com", role="super_admin")
        db_session.commit()

        resp = client.post("/api/market-releases/999999/approve", cookies=auth_admin_cookie(admin))
        assert resp.status_code == 404

    def test_plain_admin_is_forbidden(self, client, db_session: Session):
        admin = _make_admin(db_session, email="mr-plain@test.com", role="admin")
        db_session.commit()

        resp = client.get("/api/market-releases", cookies=auth_admin_cookie(admin))
        assert resp.status_code == 403

        resp2 = client.post(
            "/api/market-releases", json={"jurisdiction": "IN-MH"}, cookies=auth_admin_cookie(admin)
        )
        assert resp2.status_code == 403

    def test_requires_auth(self, client):
        resp = client.get("/api/market-releases")
        assert resp.status_code == 401
