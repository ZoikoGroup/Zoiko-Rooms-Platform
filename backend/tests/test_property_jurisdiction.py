"""Per-property region: hosts pick an open region, market release and
agreement clauses resolve from it, and it locks once something is bound to
its rules. See services/jurisdictions.py."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.crud import agreement_clauses as clause_crud
from app.crud.listing import resolve_market_release
from app.models.agreement_clause import ClauseDefinition
from app.models.listing import Listing
from app.models.market_policy import MarketPolicyPack
from app.models.market_release import MarketRelease
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from app.services import jurisdictions as jurisdiction_service
from app.services.agreement_profile import (
    DEFAULT_CLAUSES,
    agreement_profile_block_reason,
    jurisdiction_has_agreement_registry,
    resolve_agreement_profile,
)
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

# conftest seeds market policy packs for "England" and "IN" only.
SCOTLAND = "GB-SCT"


def _open_market(db: Session, code: str, *, with_policy_pack: bool = True) -> MarketRelease:
    release = MarketRelease(jurisdiction=code, status="active", min_stay_nights=30, effective_from=datetime.now(timezone.utc))
    db.add(release)
    if with_policy_pack and code not in ("England", "IN"):
        db.add(MarketPolicyPack(jurisdiction_code=code, version=1, effective_from=date(2026, 1, 1)))
    db.flush()
    return release


def _make_host(db: Session, *, email: str = "region-host@test.com", party_jurisdiction: str = "England") -> UserAccount:
    party = Party(party_type="provider", status="active", jurisdiction=party_jurisdiction)
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    db.commit()
    return user


def _make_room(db: Session, host: UserAccount, *, jurisdiction_code: str) -> Room:
    prop = Property(owner_party_id=host.party_id, address="1 Region Rd", city="Edinburgh", jurisdiction_code=jurisdiction_code)
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=False, status="active")
    db.add(room)
    db.commit()
    return room


def _make_listing(db: Session, room: Room, *, state: str = "PUBLISHED") -> Listing:
    admin = _make_admin(db, email=f"listing-owner-{room.id}@test.com")
    listing = Listing(
        id=f"L-REGION-{room.id}", slug=f"region-{room.id}", name="Region Listing", room_type="Private room",
        city="Edinburgh", location="Leith", price_per_night=50, guests=1, owner_id=admin.id, room_id=room.id, state=state,
    )
    db.add(listing)
    db.commit()
    return listing


class TestOpenJurisdictions:
    def test_region_needs_both_an_active_release_and_a_policy_pack(self, db_session: Session):
        assert jurisdiction_service.list_open_jurisdictions(db_session) == []

        _open_market(db_session, "England")
        _open_market(db_session, SCOTLAND, with_policy_pack=False)
        db_session.add(MarketRelease(jurisdiction="Wales", status="draft", min_stay_nights=30))
        db_session.add(MarketPolicyPack(jurisdiction_code="Wales", version=1, effective_from=date(2026, 1, 1)))
        db_session.commit()

        assert [j.code for j in jurisdiction_service.list_open_jurisdictions(db_session)] == ["England"]

    def test_emergency_blocked_policy_pack_closes_the_region(self, db_session: Session):
        _open_market(db_session, SCOTLAND)
        db_session.add(MarketPolicyPack(
            jurisdiction_code=SCOTLAND, version=2, effective_from=date(2026, 1, 1), confidence="EMERGENCY_BLOCK",
        ))
        db_session.commit()
        assert jurisdiction_service.list_open_jurisdictions(db_session) == []

    def test_agreements_supported_flag_reflects_clause_registry(self, db_session: Session):
        _open_market(db_session, "England")
        _open_market(db_session, SCOTLAND)
        db_session.commit()
        flags = {j.code: j.agreements_supported for j in jurisdiction_service.list_open_jurisdictions(db_session)}
        assert flags == {"England": True, SCOTLAND: False}

    def test_host_endpoint_lists_open_regions(self, client, db_session: Session):
        _open_market(db_session, "England")
        db_session.commit()
        host = _make_host(db_session)
        r = client.get("/api/users/hosting/jurisdictions", cookies=auth_user_cookie(host))
        assert r.status_code == 200, r.text
        assert r.json() == [{"code": "England", "minStayNights": 30, "marketPolicyVersion": 1, "agreementsSupported": True}]


class TestHostPropertyRegion:
    def test_create_requires_a_region(self, client, db_session: Session):
        _open_market(db_session, "England")
        host = _make_host(db_session)
        r = client.post(
            "/api/users/hosting/properties", json={"address": "1 A St", "city": "London"}, cookies=auth_user_cookie(host),
        )
        assert r.status_code == 422, r.text

    def test_create_rejects_a_region_that_is_not_open(self, client, db_session: Session):
        _open_market(db_session, "England")
        host = _make_host(db_session)
        r = client.post(
            "/api/users/hosting/properties",
            json={"address": "1 A St", "city": "Edinburgh", "jurisdictionCode": SCOTLAND},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 400, r.text
        assert "Available regions: England" in r.json()["detail"]

    def test_create_in_an_open_non_default_region(self, client, db_session: Session):
        _open_market(db_session, "England")
        _open_market(db_session, SCOTLAND)
        db_session.commit()
        host = _make_host(db_session)
        r = client.post(
            "/api/users/hosting/properties",
            json={"address": "1 A St", "city": "Edinburgh", "jurisdictionCode": f" {SCOTLAND} "},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 201, r.text
        assert r.json()["jurisdictionCode"] == SCOTLAND
        assert r.json()["regionLocked"] is False

    def test_region_can_change_until_a_listing_goes_live(self, client, db_session: Session):
        _open_market(db_session, "England")
        _open_market(db_session, SCOTLAND)
        host = _make_host(db_session)
        room = _make_room(db_session, host, jurisdiction_code="England")
        url = f"/api/users/hosting/properties/{room.property_id}"
        cookies = auth_user_cookie(host)

        r = client.put(url, json={"address": "1 Region Rd", "city": "Edinburgh", "jurisdictionCode": SCOTLAND}, cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["jurisdictionCode"] == SCOTLAND

        _make_listing(db_session, room, state="PUBLISHED")
        r = client.put(url, json={"address": "1 Region Rd", "city": "Edinburgh", "jurisdictionCode": "England"}, cookies=cookies)
        assert r.status_code == 409, r.text

        # Editing the address with the region unchanged still works once locked.
        r = client.put(url, json={"address": "2 Region Rd", "city": "Edinburgh", "jurisdictionCode": SCOTLAND}, cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["address"] == "2 Region Rd"
        assert r.json()["regionLocked"] is True

    def test_draft_listing_does_not_lock_the_region(self, db_session: Session):
        host = _make_host(db_session)
        room = _make_room(db_session, host, jurisdiction_code="England")
        _make_listing(db_session, room, state="DRAFT")
        assert jurisdiction_service.property_region_is_locked(db_session, room.property) is False

    def test_unchanged_region_is_accepted_after_the_market_closes(self, client, db_session: Session):
        host = _make_host(db_session)
        room = _make_room(db_session, host, jurisdiction_code=SCOTLAND)
        r = client.put(
            f"/api/users/hosting/properties/{room.property_id}",
            json={"address": "3 Region Rd", "city": "Edinburgh", "jurisdictionCode": SCOTLAND},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 200, r.text


class TestAdminPropertyRegion:
    def test_admin_create_validates_region(self, client, db_session: Session):
        _open_market(db_session, "England")
        admin = _make_admin(db_session, email="region-admin@test.com", role="super_admin")
        db_session.commit()
        cookies = auth_admin_cookie(admin)

        r = client.post("/api/properties", json={"address": "1 A St", "city": "Paris", "jurisdictionCode": "FR"}, cookies=cookies)
        assert r.status_code == 400, r.text

        r = client.post("/api/properties", json={"address": "1 A St", "city": "London", "jurisdictionCode": "England"}, cookies=cookies)
        assert r.status_code == 201, r.text

        r = client.get("/api/properties/jurisdictions", cookies=cookies)
        assert r.status_code == 200, r.text
        assert [j["code"] for j in r.json()] == ["England"]


class TestMarketReleaseFromProperty:
    def test_listing_market_comes_from_the_property_region(self, db_session: Session):
        _open_market(db_session, "England")
        scotland = _open_market(db_session, SCOTLAND)
        host = _make_host(db_session, party_jurisdiction="England")
        room = _make_room(db_session, host, jurisdiction_code=SCOTLAND)
        listing = _make_listing(db_session, room)
        assert resolve_market_release(db_session, listing).id == scotland.id

    def test_falls_back_to_party_region_when_property_region_has_no_market(self, db_session: Session):
        india = _open_market(db_session, "IN")
        host = _make_host(db_session, party_jurisdiction="IN")
        room = _make_room(db_session, host, jurisdiction_code="England")
        listing = _make_listing(db_session, room)
        assert resolve_market_release(db_session, listing).id == india.id


class TestAgreementClausesPerRegion:
    def _approve_all(self, db: Session, admin, rows):
        for row in rows:
            clause_crud.approve_clause_version(db, admin, row)

    def test_new_region_blocks_agreements_until_its_clauses_are_approved(self, db_session: Session):
        scotland = _open_market(db_session, SCOTLAND)
        host = _make_host(db_session)
        room = _make_room(db_session, host, jurisdiction_code=SCOTLAND)
        listing = _make_listing(db_session, room)
        listing.market_release_id = scotland.id
        admin = _make_admin(db_session, email="clause-admin@test.com", role="super_admin")
        db_session.commit()

        assert resolve_agreement_profile(db_session, listing, room) is None
        assert "has no approved agreement clauses yet" in agreement_profile_block_reason(db_session, listing, room)

        drafts = clause_crud.copy_default_clauses_to_jurisdiction(db_session, admin, SCOTLAND)
        assert {d.clause_id for d in drafts} == {clause_id for clause_id, _, _ in DEFAULT_CLAUSES}
        assert all(d.status == "DRAFT" and d.version == 1 and d.jurisdiction_scope == SCOTLAND for d in drafts)
        # Drafts alone don't make agreements possible.
        assert resolve_agreement_profile(db_session, listing, room) is None

        self._approve_all(db_session, admin, drafts)
        profile = resolve_agreement_profile(db_session, listing, room)
        assert profile is not None
        assert profile.jurisdiction == SCOTLAND
        assert agreement_profile_block_reason(db_session, listing, room) is None

    def test_copy_defaults_is_idempotent_and_refuses_england(self, db_session: Session):
        admin = _make_admin(db_session, email="clause-admin2@test.com", role="super_admin")
        db_session.commit()
        assert len(clause_crud.copy_default_clauses_to_jurisdiction(db_session, admin, SCOTLAND)) == len(DEFAULT_CLAUSES)
        assert clause_crud.copy_default_clauses_to_jurisdiction(db_session, admin, SCOTLAND) == []

        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            clause_crud.copy_default_clauses_to_jurisdiction(db_session, admin, "England")

    def test_approving_a_region_clause_does_not_retire_englands(self, db_session: Session):
        admin = _make_admin(db_session, email="clause-admin3@test.com", role="super_admin")
        db_session.commit()
        assert jurisdiction_has_agreement_registry(db_session, "England")

        drafts = clause_crud.copy_default_clauses_to_jurisdiction(db_session, admin, SCOTLAND)
        self._approve_all(db_session, admin, drafts)

        england_rent = db_session.query(ClauseDefinition).filter_by(
            clause_id="rent_and_charges", jurisdiction_scope="England",
        ).one()
        assert england_rent.status == "APPROVED"
        assert jurisdiction_has_agreement_registry(db_session, "England")
        assert jurisdiction_has_agreement_registry(db_session, SCOTLAND)

    def test_copy_defaults_endpoint(self, client, db_session: Session):
        admin = _make_admin(db_session, email="clause-admin4@test.com", role="super_admin")
        db_session.commit()
        r = client.post(
            "/api/leasing/agreement-clauses/copy-defaults", json={"jurisdictionScope": SCOTLAND},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert len(r.json()) == len(DEFAULT_CLAUSES)

        r = client.get(f"/api/leasing/agreement-clauses?jurisdiction_scope={SCOTLAND}", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert {row["jurisdictionScope"] for row in r.json()} == {SCOTLAND}
