"""ZR-ENG-CLR-001 Section 1, Rule 6 / Section 9 / AC-11: occupant-level
overlap risk (app/services/overlap.py), evaluated at offer acceptance
(crud/leasing.py:_accept_offer_and_hold_room) rather than at the account/
payer level.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.leasing import Offer
from app.models.listing import Listing
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_room_hold_atomicity import _make_verified_renter


def _make_listing_with_room(db: Session, *, suffix: str) -> tuple[str, int]:
    """Own copy of test_room_hold_atomicity's helper with a caller-supplied
    suffix -- these tests need two distinct listings/rooms per test, and that
    helper hardcodes a single fixed id/slug."""
    provider_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(provider_party)
    db.flush()
    prop = Property(owner_party_id=provider_party.id, address=f"1 Overlap St {suffix}", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    listing = Listing(
        id=f"L-OVERLAP-{suffix}", slug=f"overlap-{suffix}", name=f"Overlap Test Listing {suffix}", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=provider_party.id, owner_id=None,
        room_id=room.id, state="PUBLISHED",
    )
    db.add(listing)
    db.commit()
    return listing.id, room.id


def _apply_add_terms_and_send(
    client, admin_cookies: dict, renter, listing_id: str, *, start_date: date, term_months: int = 6,
    named_occupant_guest_id: str | None = None,
) -> tuple[int, int]:
    payload = {"listingId": listing_id, "message": "Interested", "desiredMoveIn": None}
    if named_occupant_guest_id is not None:
        payload["namedOccupantGuestId"] = named_occupant_guest_id
    r = client.post("/api/users/rentals/applications", json=payload, cookies=auth_user_cookie(renter))
    assert r.status_code == 201, r.text
    application_id = r.json()["id"]

    r = client.post(
        f"/api/leasing/applications/{application_id}/decide", json={"decision": "APPROVED"}, cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text

    r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    offer_id = r.json()["id"]

    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={
            "monthlyRent": 500, "depositAmount": 500,
            "startDate": start_date.isoformat(), "termMonths": term_months,
        },
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text

    r = client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    return application_id, offer_id


class TestNoOverlap:
    def test_non_overlapping_terms_for_same_occupant_are_unrisked(self, client, db_session: Session):
        listing_a, _ = _make_listing_with_room(db_session, suffix="1a")
        listing_b, _ = _make_listing_with_room(db_session, suffix="1b")
        super_admin = _make_admin(db_session, email="overlap-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="overlap-renter1@test.com")

        start_a = date.today() + timedelta(days=10)
        _app_a, offer_a = _apply_add_terms_and_send(client, admin_cookies, renter, listing_a, start_date=start_a, term_months=6)
        r = client.post(f"/api/users/rentals/offers/{offer_a}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["occupantRiskTier"] == "NONE"

        # Second listing, term starts well after the first one ends -- no overlap.
        start_b = start_a + timedelta(days=6 * 31 + 30)
        _app_b, offer_b = _apply_add_terms_and_send(client, admin_cookies, renter, listing_b, start_date=start_b, term_months=6)
        r = client.post(f"/api/users/rentals/offers/{offer_b}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["occupantRiskTier"] == "NONE"


class TestNearFullOverlapBlocksUnlessOverridden:
    def test_same_occupant_overlapping_terms_on_different_listing_is_blocked(self, client, db_session: Session):
        listing_a, _ = _make_listing_with_room(db_session, suffix="2a")
        listing_b, _ = _make_listing_with_room(db_session, suffix="2b")
        super_admin = _make_admin(db_session, email="overlap-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="overlap-renter2@test.com")

        start = date.today() + timedelta(days=10)
        _app_a, offer_a = _apply_add_terms_and_send(client, admin_cookies, renter, listing_a, start_date=start, term_months=6)
        r = client.post(f"/api/users/rentals/offers/{offer_a}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        # Same renter, same near-identical 6-month window, a DIFFERENT listing.
        _app_b, offer_b = _apply_add_terms_and_send(client, admin_cookies, renter, listing_b, start_date=start, term_months=6)
        r = client.post(f"/api/users/rentals/offers/{offer_b}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 409, r.text

        # The renter can self-declare an exception reason to proceed anyway
        # (Section 9: "Block confirmation or require exception") -- captured
        # on the offer for later admin review, not a silent bypass.
        r = client.post(
            f"/api/users/rentals/offers/{offer_b}/accept",
            json={"overrideReason": "This is a planned relocation -- confirmed by phone"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 200, r.text
        assert r.json()["occupantRiskTier"] == "BLOCK"
        assert "override" in r.json()["occupantRiskReason"].lower()


class TestShortRelocationOverlapIsAllowedButFlagged:
    def test_short_tail_overlap_is_reviewed_not_blocked(self, client, db_session: Session):
        listing_a, _ = _make_listing_with_room(db_session, suffix="3a")
        listing_b, _ = _make_listing_with_room(db_session, suffix="3b")
        super_admin = _make_admin(db_session, email="overlap-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="overlap-renter3@test.com")

        start_a = date.today() + timedelta(days=10)
        _app_a, offer_a = _apply_add_terms_and_send(client, admin_cookies, renter, listing_a, start_date=start_a, term_months=6)
        r = client.post(f"/api/users/rentals/offers/{offer_a}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        # Listing B starts 5 days before listing A ends -- a brief relocation
        # overlap well inside the 14-day tolerance, not a near-full duplicate.
        offer_a_row = db_session.get(Offer, offer_a)
        end_a = offer_a_row.terms[-1].start_date + timedelta(days=6 * 30)
        start_b = end_a - timedelta(days=5)
        _app_b, offer_b = _apply_add_terms_and_send(client, admin_cookies, renter, listing_b, start_date=start_b, term_months=6)

        r = client.post(f"/api/users/rentals/offers/{offer_b}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["occupantRiskTier"] == "REVIEW"


class TestNamedOccupantIsWhatOverlapKeysOff:
    def test_different_payers_booking_the_same_named_occupant_are_flagged(self, client, db_session: Session):
        """Account holder/payer and Named Occupant are distinct roles (Rule 6)
        -- two different Zoiko accounts booking overlapping terms for the SAME
        real occupant must still be caught, even though the payer differs."""
        listing_a, _ = _make_listing_with_room(db_session, suffix="4a")
        listing_b, _ = _make_listing_with_room(db_session, suffix="4b")
        super_admin = _make_admin(db_session, email="overlap-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        payer_one = _make_verified_renter(db_session, email="overlap-payer1@test.com")
        payer_two = _make_verified_renter(db_session, email="overlap-payer2@test.com")

        start = date.today() + timedelta(days=10)
        _app_a, offer_a = _apply_add_terms_and_send(client, admin_cookies, payer_one, listing_a, start_date=start, term_months=6)
        r = client.post(f"/api/users/rentals/offers/{offer_a}/accept", cookies=auth_user_cookie(payer_one))
        assert r.status_code == 200, r.text
        payer_one_guest_id = db_session.get(Offer, offer_a).guest_id

        # payer_two applies to a DIFFERENT listing, naming payer_one's own
        # guest record as the actual occupant -- same person, overlapping term.
        _app_b, offer_b = _apply_add_terms_and_send(
            client, admin_cookies, payer_two, listing_b, start_date=start, term_months=6,
            named_occupant_guest_id=payer_one_guest_id,
        )
        r = client.post(f"/api/users/rentals/offers/{offer_b}/accept", cookies=auth_user_cookie(payer_two))
        assert r.status_code == 409, r.text
