"""GET /api/public/listings feeds the Find a Room page: paging must be stable, and a
search's own % and _ must be taken literally."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.test_search_orchestrator import _seed_listing

URL = "/api/public/listings"


def _ids(client, **params) -> list[str]:
    page = client.get(URL, params=params).json()
    return [item["id"] for item in page["items"]]


def test_same_named_listings_never_repeat_or_vanish_across_pages(client, db_session: Session):
    # Eleven listings that all share one name: ordering by name alone can't tell them apart.
    for n in range(11):
        _seed_listing(db_session, slug=f"same-{n:02d}", name="Sunny Room", city="Leeds")
    db_session.commit()

    seen: list[str] = []
    offset = 0
    while True:
        page = client.get(URL, params={"limit": 3, "offset": offset}).json()
        seen += [item["id"] for item in page["items"]]
        if not page["hasMore"]:
            break
        offset += 3
    assert len(seen) == 11 and len(set(seen)) == 11
    # And it is the same fixed order every time.
    assert _ids(client, limit=11) == seen


def test_every_listing_is_reachable_by_paging_through(client, db_session: Session):
    for n in range(7):
        _seed_listing(db_session, slug=f"reach-{n}", name=f"Room {n}", city="York")
    db_session.commit()
    first = client.get(URL, params={"limit": 4}).json()
    assert first["total"] == 7 and first["hasMore"] is True
    second = client.get(URL, params={"limit": 4, "offset": 4}).json()
    assert len(second["items"]) == 3 and second["hasMore"] is False


def test_percent_and_underscore_in_a_search_are_literal(client, db_session: Session):
    _seed_listing(db_session, slug="lit-1", name="One", city="Leeds")
    _seed_listing(db_session, slug="lit-2", name="Two", city="York")
    _seed_listing(db_session, slug="lit-3", name="Three", city="50%_Off Town")
    db_session.commit()

    # "%" used to match every city; now it only matches a city that really contains a "%".
    assert _ids(client, city="%") == ["lit-3"]
    assert _ids(client, city="_") == ["lit-3"]
    assert _ids(client, city="50%_") == ["lit-3"]
    # Ordinary searches still work, case-insensitively and anywhere in the name.
    assert _ids(client, city="eeds") == ["lit-1"]
    assert _ids(client, city="YORK") == ["lit-2"]
