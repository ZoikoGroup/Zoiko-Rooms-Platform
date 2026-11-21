"""Every Find a Room filter reads a value the host gives in the listing form. These tests check that each of
them is saved as the right kind of value and comes back from the public API in a form the filters understand."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.crud.listing import normalize_room_type
from app.models.listing import Listing
from tests.test_availability import LISTING_PAYLOAD, _create_publish_listing, _make_host_with_room
from tests.conftest import auth_user_cookie


@pytest.mark.parametrize("raw,expected", [
    ("private_room", "Private room"), ("Private room", "Private room"), ("  private   room ", "Private room"),
    ("studio", "Studio"), ("En-suite", "En-suite"), ("", ""),
])
def test_room_type_is_stored_in_one_spelling(raw, expected):
    assert normalize_room_type(raw) == expected


def test_every_filter_field_is_saved_and_served_correctly(client, db_session):
    user, _prop, room_id = _make_host_with_room(db_session)
    available_from = (date.today() + timedelta(days=10)).isoformat()
    r = client.post(
        "/api/users/hosting/listings",
        json={
            **LISTING_PAYLOAD,
            "roomId": room_id,
            "roomType": "private_room",
            "currency": "GBP",
            "pricePerNight": 45,
            "minStayNights": 90,
            "availableFrom": available_from,
            "bathrooms": 1,
            "amenities": ["Furnished", "Wi-Fi", "Lift / Elevator", "Step-free Entrance"],
        },
        cookies=auth_user_cookie(user),
    )
    assert r.status_code == 201, r.text
    listing_id = r.json()["id"]

    # What the database holds -- real types, not strings of them.
    row = db_session.get(Listing, listing_id)
    assert row.room_type == "Private room"
    assert row.currency == "GBP"
    assert row.price_per_night == 45
    assert row.min_stay_nights == 90
    assert row.available_from == date.fromisoformat(available_from)
    assert isinstance(row.amenities, list) and row.amenities == ["Furnished", "Wi-Fi", "Lift / Elevator", "Step-free Entrance"]
    assert row.bathrooms == 1
    assert row.has_ensuite is True  # from the room the host chose

    # And what the public (Find a Room) API returns once published.
    from tests.conftest import _make_admin, auth_admin_cookie

    admin = _make_admin(db_session, email="admin-filters@test.com", role="super_admin")
    assert client.post(f"/api/listings/{listing_id}/publish", cookies=auth_admin_cookie(admin)).status_code == 200
    items = client.get("/api/public/listings").json()["items"]
    item = next(i for i in items if i["id"] == listing_id)
    assert item["roomType"] == "Private room"
    assert item["currency"] == "GBP"
    assert item["minStayNights"] == 90
    assert item["availableFrom"] == available_from
    assert item["hasEnsuite"] is True
    assert "Furnished" in item["amenities"] and "Step-free Entrance" in item["amenities"]


def test_a_shared_bathroom_room_is_reported_as_not_ensuite(client, db_session):
    from app.models.room import Room

    user, _prop, room_id = _make_host_with_room(db_session, email="shared-host@test.com")
    db_session.get(Room, room_id).has_ensuite = False
    db_session.commit()
    r = client.post("/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=auth_user_cookie(user))
    assert r.status_code == 201, r.text
    assert r.json()["hasEnsuite"] is False


def test_editing_the_room_type_is_normalised_too(client, db_session):
    user, _prop, room_id = _make_host_with_room(db_session, email="edit-host@test.com")
    cookies = auth_user_cookie(user)
    listing_id = client.post("/api/users/hosting/listings", json={**LISTING_PAYLOAD, "roomId": room_id}, cookies=cookies).json()["id"]
    r = client.put(f"/api/users/hosting/listings/{listing_id}", json={"roomType": "double_room"}, cookies=cookies)
    assert r.status_code == 200, r.text
    assert db_session.get(Listing, listing_id).room_type == "Double room"
