"""Coverage gap found during a full backend read-through: /api/market-releases
(super-admin approve/disable workflow for a listing's market) had zero test
coverage of its own endpoints anywhere in the suite -- notable since this
router gates whether a whole market/jurisdiction can accept listings at all."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.market_release import MarketRelease
from tests.conftest import _make_admin, auth_admin_cookie


class TestMarketReleasesRequireSuperAdmin:
    def test_plain_admin_cannot_list_market_releases(self, client, db_session: Session):
        admin = _make_admin(db_session, email="mr-plain@test.com", role="admin")
        r = client.get("/api/market-releases", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text

    def test_plain_admin_cannot_create_market_release(self, client, db_session: Session):
        admin = _make_admin(db_session, email="mr-plain2@test.com", role="admin")
        r = client.post(
            "/api/market-releases", json={"jurisdiction": "IN-TEST-1"}, cookies=auth_admin_cookie(admin)
        )
        assert r.status_code == 403, r.text

    def test_plain_admin_cannot_approve_market_release(self, client, db_session: Session):
        release = MarketRelease(jurisdiction="IN-TEST-2", status="draft")
        db_session.add(release)
        db_session.commit()
        admin = _make_admin(db_session, email="mr-plain3@test.com", role="admin")
        r = client.post(f"/api/market-releases/{release.id}/approve", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text


class TestMarketReleaseLifecycle:
    def test_super_admin_can_create_approve_and_disable_a_release(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="mr-super@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)

        r = client.post("/api/market-releases", json={"jurisdiction": "IN-TEST-3", "minStayNights": 45}, cookies=cookies)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "draft"
        assert body["minStayNights"] == 45
        release_id = body["id"]

        r = client.get("/api/market-releases", cookies=cookies)
        assert r.status_code == 200, r.text
        assert any(rel["id"] == release_id for rel in r.json())

        r = client.post(f"/api/market-releases/{release_id}/approve", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"
        assert r.json()["approvedAt"] is not None

        r = client.post(f"/api/market-releases/{release_id}/disable", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "disabled"

    def test_approving_unknown_release_is_404(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="mr-super2@test.com", role="super_admin")
        r = client.post("/api/market-releases/999999/approve", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 404, r.text
