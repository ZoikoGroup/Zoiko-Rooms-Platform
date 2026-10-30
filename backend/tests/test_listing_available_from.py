"""A host can say when a room can be moved into; the public listing carries it, and changing it
never forces a re-review."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.listing import Listing
from app.models.listing_version import ListingVersion
from tests.conftest import auth_user_cookie
from tests.test_self_listing_restriction import _make_host_and_listing, _make_verified_renter

PUBLIC = "/api/public/listings"


def _public_item(client, listing_id: str, cookies=None) -> dict:
    items = client.get(PUBLIC, cookies=cookies).json()["items"]
    return next(i for i in items if i["id"] == listing_id)


def test_a_room_with_no_date_is_available_now(client, db_session: Session):
    _host, listing_id = _make_host_and_listing(db_session, email="af-host1@test.com")
    other = _make_verified_renter(db_session, email="af-viewer1@test.com")
    assert _public_item(client, listing_id, auth_user_cookie(other))["availableFrom"] is None


def test_host_sets_and_clears_the_date_and_it_reaches_the_public_listing(client, db_session: Session):
    host, listing_id = _make_host_and_listing(db_session, email="af-host2@test.com")
    viewer = auth_user_cookie(_make_verified_renter(db_session, email="af-viewer2@test.com"))
    host_cookies = auth_user_cookie(host)
    later = (date.today() + timedelta(days=45)).isoformat()

    r = client.put(f"/api/users/hosting/listings/{listing_id}", json={"availableFrom": later}, cookies=host_cookies)
    assert r.status_code == 200, r.text
    assert r.json()["availableFrom"] == later
    assert _public_item(client, listing_id, viewer)["availableFrom"] == later

    # Sending null puts it back to "available now".
    r = client.put(f"/api/users/hosting/listings/{listing_id}", json={"availableFrom": None}, cookies=host_cookies)
    assert r.status_code == 200 and r.json()["availableFrom"] is None
    assert _public_item(client, listing_id, viewer)["availableFrom"] is None


def test_changing_the_date_is_not_a_material_edit_and_leaves_the_listing_published(client, db_session: Session):
    host, listing_id = _make_host_and_listing(db_session, email="af-host3@test.com")
    later = (date.today() + timedelta(days=20)).isoformat()
    assert client.put(
        f"/api/users/hosting/listings/{listing_id}", json={"availableFrom": later}, cookies=auth_user_cookie(host),
    ).status_code == 200

    db_session.expire_all()
    listing = db_session.get(Listing, listing_id)
    assert listing.state == "PUBLISHED" and listing.available_from.isoformat() == later
    versions = db_session.query(ListingVersion).filter_by(listing_id=listing_id).all()
    assert not any(v.is_material for v in versions)


def test_other_edits_leave_the_date_alone(client, db_session: Session):
    host, listing_id = _make_host_and_listing(db_session, email="af-host4@test.com")
    cookies = auth_user_cookie(host)
    later = (date.today() + timedelta(days=30)).isoformat()
    client.put(f"/api/users/hosting/listings/{listing_id}", json={"availableFrom": later}, cookies=cookies)
    r = client.put(f"/api/users/hosting/listings/{listing_id}", json={"description": "Newly painted"}, cookies=cookies)
    assert r.status_code == 200 and r.json()["availableFrom"] == later
