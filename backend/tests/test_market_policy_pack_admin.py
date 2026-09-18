"""Admin CRUD for MarketPolicyPack -- previously only creatable inside test
fixtures, with no real production surface. Every jurisdiction-aware domain
(deposit, sublet, rent-change, occupancy eligibility, property compliance,
screening) resolves rules from this table; this is the actual admin
capability needed for any of those to be configurable in production."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market_policy import MarketPolicyPack
from tests.conftest import _make_admin, auth_admin_cookie


class TestMarketPolicyPackAdminAccess:
    def test_plain_admin_cannot_create_a_pack(self, client, db_session: Session):
        admin = _make_admin(db_session, email="mpp-plain-01@test.com", role="admin")
        r = client.post(
            "/api/market-policy-packs",
            json={"jurisdictionCode": "TestJur", "effectiveFrom": date.today().isoformat()},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 403, r.text

    def test_plain_admin_cannot_list_packs(self, client, db_session: Session):
        admin = _make_admin(db_session, email="mpp-plain-02@test.com", role="admin")
        r = client.get("/api/market-policy-packs", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text


class TestMarketPolicyPackCreate:
    def test_super_admin_can_create_a_pack(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="mpp-super-01@test.com", role="super_admin")
        r = client.post(
            "/api/market-policy-packs",
            json={
                "jurisdictionCode": "France", "effectiveFrom": date.today().isoformat(),
                "confidence": "REVIEW_REQUIRED", "legalSourceNote": "test source",
            },
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["version"] == 1
        assert r.json()["confidence"] == "REVIEW_REQUIRED"

    def test_creating_again_for_same_jurisdiction_bumps_version(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="mpp-super-02@test.com", role="super_admin")
        payload = {"jurisdictionCode": "TestJur2", "effectiveFrom": date.today().isoformat()}
        r1 = client.post("/api/market-policy-packs", json=payload, cookies=auth_admin_cookie(super_admin))
        assert r1.json()["version"] == 1
        r2 = client.post("/api/market-policy-packs", json=payload, cookies=auth_admin_cookie(super_admin))
        assert r2.json()["version"] == 2

    def test_rejects_invalid_confidence(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="mpp-super-03@test.com", role="super_admin")
        r = client.post(
            "/api/market-policy-packs",
            json={"jurisdictionCode": "TestJur3", "effectiveFrom": date.today().isoformat(), "confidence": "MADE_UP"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 400, r.text

    def test_rejects_jurisdiction_code_too_long(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="mpp-super-04@test.com", role="super_admin")
        r = client.post(
            "/api/market-policy-packs",
            json={"jurisdictionCode": "WayTooLongCode", "effectiveFrom": date.today().isoformat()},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 400, r.text


class TestMarketPolicyPackUpdateAndList:
    def test_update_edits_existing_pack_in_place(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="mpp-super-05@test.com", role="super_admin")
        r = client.post(
            "/api/market-policy-packs",
            json={"jurisdictionCode": "TestJur5", "effectiveFrom": date.today().isoformat()},
            cookies=auth_admin_cookie(super_admin),
        )
        pack_id = r.json()["id"]

        r = client.patch(
            f"/api/market-policy-packs/{pack_id}", json={"confidence": "VERIFIED", "legalSourceNote": "reviewed by counsel"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["confidence"] == "VERIFIED"
        assert r.json()["legalSourceNote"] == "reviewed by counsel"

    def test_list_filters_by_jurisdiction(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="mpp-super-06@test.com", role="super_admin")
        client.post("/api/market-policy-packs", json={"jurisdictionCode": "TestJur6", "effectiveFrom": date.today().isoformat()}, cookies=auth_admin_cookie(super_admin))
        client.post("/api/market-policy-packs", json={"jurisdictionCode": "TestJur7", "effectiveFrom": date.today().isoformat()}, cookies=auth_admin_cookie(super_admin))

        r = client.get("/api/market-policy-packs?jurisdictionCode=TestJur6", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert all(p["jurisdictionCode"] == "TestJur6" for p in r.json())
        assert len(r.json()) >= 1

    def test_created_pack_is_actually_resolvable(self, client, db_session: Session):
        """The whole point -- resolve_market_policy must see what the admin
        API created, since deposit/sublet/occupancy-eligibility/etc. all
        read through that one function."""
        from app.crud.market_policy import resolve_market_policy

        super_admin = _make_admin(db_session, email="mpp-super-07@test.com", role="super_admin")
        r = client.post(
            "/api/market-policy-packs",
            json={"jurisdictionCode": "TestJur8", "effectiveFrom": date.today().isoformat(), "occupancyEligibilityRequired": True},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 201, r.text

        resolved = resolve_market_policy(db_session, "TestJur8")
        assert resolved.occupancy_eligibility_required is True
