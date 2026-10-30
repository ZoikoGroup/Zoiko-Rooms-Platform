"""A host can set how many people may live in a room. That limit is what decides whether a renter can
sublease part of the room or add a co-tenant -- it used to be fixed at 1, so those options were
permanently greyed out as 'room is full'."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.occupancy import Occupancy
from tests.conftest import auth_user_cookie
from tests.test_host_move_in_confirmation import _drive_agreement_to_signed


def _tenancy(client, db_session: Session, tag: str):
    host, renter, host_cookies, renter_cookies, occupancy_id = _drive_agreement_to_signed(
        client, db_session, host_email=f"mo-{tag}-host@test.com", renter_email=f"mo-{tag}-renter@test.com",
    )
    assert client.post(f"/api/users/hosting/occupancies/{occupancy_id}/confirm-move-in", cookies=host_cookies).status_code == 200
    occupancy = db_session.get(Occupancy, occupancy_id)
    return host_cookies, renter_cookies, occupancy.room.property_id, occupancy.room_id


def _room_url(property_id: int, room_id: int) -> str:
    return f"/api/users/hosting/properties/{property_id}/rooms/{room_id}"


def _renter_sees_space(client, renter_cookies) -> bool:
    [rental] = client.get("/api/users/rentals/occupancies", cookies=renter_cookies).json()
    return rental["roomHasSpaceForCoTenant"]


class TestRoomMaxOccupants:
    def test_a_new_room_defaults_to_one_and_a_host_can_choose_more(self, client, db_session: Session):
        host_cookies, _rc, property_id, _room_id = _tenancy(client, db_session, "create")
        r = client.post(f"/api/users/hosting/properties/{property_id}/rooms", json={"size": 100, "hasEnsuite": False}, cookies=host_cookies)
        assert r.status_code == 201 and r.json()["maxOccupants"] == 1
        r = client.post(
            f"/api/users/hosting/properties/{property_id}/rooms", json={"size": 100, "hasEnsuite": False, "maxOccupants": 3},
            cookies=host_cookies,
        )
        assert r.status_code == 201 and r.json()["maxOccupants"] == 3

    def test_raising_the_limit_is_what_opens_partial_sublease_and_co_tenants_to_the_renter(self, client, db_session: Session):
        host_cookies, renter_cookies, property_id, room_id = _tenancy(client, db_session, "open")
        assert _renter_sees_space(client, renter_cookies) is False  # one room, one occupant: "room is full"

        r = client.put(_room_url(property_id, room_id), json={"size": 120, "hasEnsuite": True, "maxOccupants": 2}, cookies=host_cookies)
        assert r.status_code == 200 and r.json()["maxOccupants"] == 2
        assert _renter_sees_space(client, renter_cookies) is True

        # Back to one: no space again.
        r = client.put(_room_url(property_id, room_id), json={"size": 120, "hasEnsuite": True, "maxOccupants": 1}, cookies=host_cookies)
        assert r.status_code == 200
        assert _renter_sees_space(client, renter_cookies) is False

    def test_editing_other_room_details_does_not_reset_the_limit(self, client, db_session: Session):
        host_cookies, _rc, property_id, room_id = _tenancy(client, db_session, "keep")
        client.put(_room_url(property_id, room_id), json={"size": 120, "hasEnsuite": True, "maxOccupants": 2}, cookies=host_cookies)
        r = client.put(_room_url(property_id, room_id), json={"size": 150, "hasEnsuite": False}, cookies=host_cookies)
        assert r.status_code == 200 and r.json()["maxOccupants"] == 2 and r.json()["size"] == 150

    def test_the_limit_cannot_go_below_the_people_already_there(self, client, db_session: Session, monkeypatch):
        host_cookies, _rc, property_id, room_id = _tenancy(client, db_session, "floor")
        client.put(_room_url(property_id, room_id), json={"size": 120, "hasEnsuite": True, "maxOccupants": 3}, cookies=host_cookies)
        monkeypatch.setattr("app.crud.eligibility.room_occupant_count", lambda db, room, **kw: 2)  # two people live there
        r = client.put(_room_url(property_id, room_id), json={"size": 120, "hasEnsuite": True, "maxOccupants": 1}, cookies=host_cookies)
        assert r.status_code == 409 and "can't be set below 2" in r.text
        assert client.put(_room_url(property_id, room_id), json={"size": 120, "hasEnsuite": True, "maxOccupants": 2}, cookies=host_cookies).status_code == 200

    def test_nonsense_limits_are_rejected(self, client, db_session: Session):
        host_cookies, _rc, property_id, room_id = _tenancy(client, db_session, "bad")
        for bad in (0, -1, 11):
            r = client.put(_room_url(property_id, room_id), json={"size": 120, "hasEnsuite": True, "maxOccupants": bad}, cookies=host_cookies)
            assert r.status_code == 422, bad

    def test_another_host_cannot_change_it(self, client, db_session: Session):
        from tests.test_self_listing_restriction import _make_verified_renter

        _hc, _rc, property_id, room_id = _tenancy(client, db_session, "other")
        stranger = auth_user_cookie(_make_verified_renter(db_session, email="mo-stranger@test.com"))
        r = client.put(_room_url(property_id, room_id), json={"size": 120, "hasEnsuite": True, "maxOccupants": 5}, cookies=stranger)
        assert r.status_code in (403, 404)
