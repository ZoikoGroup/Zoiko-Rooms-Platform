"""ZR-ENG-CLR-008 Section 8 MVP slice: a renter requesting a new move-in date
before moving in (DATE_SHIFT) or an extended stay after moving in
(EXTENSION). Deliberately narrow -- these two change types only -- and
reuses the existing AgreementAmendment engine for the actual contract
re-papering rather than duplicating it."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.occupancy import _add_months
from app.models.booking_change_request import BookingChangeRequest
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie, deliver_all_disclosures
from tests.test_agreement_engine_extensions_2 import _full_signed_agreement, _pay_off_agreement_obligations
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible
from tests.test_room_hold_atomicity import _make_listing_with_room, _make_verified_renter


def _signed_agreement_before_move_in(client, db_session: Session, *, email_suffix: str, start_date=None):
    listing_id, _room_id = _make_listing_with_room(db_session)
    super_admin = _make_admin(db_session, email=f"bcr-admin-{email_suffix}@test.com", role="super_admin")
    admin_cookies = auth_admin_cookie(super_admin)
    renter = _make_verified_renter(db_session, email=f"bcr-renter-{email_suffix}@test.com")
    agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id, start_date=start_date)
    return agreement_id, admin_cookies, renter


def _signed_agreement_active_occupancy(client, db_session: Session, *, email_suffix: str, start_date=None):
    agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
        client, db_session, email_suffix=email_suffix, start_date=start_date,
    )
    r = client.post(f"/api/occupancy/agreements/{agreement_id}/confirm-move-in", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    return agreement_id, admin_cookies, renter


def _make_second_listing(db_session: Session, *, listing_id: str) -> str:
    """A second, independent published listing -- _make_listing_with_room
    always creates 'L-HOLDTEST1', so PREMISES_CHANGE tests (which need a
    real, different target listing) need their own second one."""
    provider_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db_session.add(provider_party)
    db_session.flush()
    prop = Property(owner_party_id=provider_party.id, address="2 Target St", city="Bengaluru", status="active")
    db_session.add(prop)
    db_session.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=90, has_ensuite=True, status="active")
    db_session.add(room)
    db_session.flush()
    listing = Listing(
        id=listing_id, slug=listing_id.lower(), name="Target Listing", room_type="Private room",
        city="Bengaluru", location="Indiranagar", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=provider_party.id, owner_id=None,
        room_id=room.id, state="PUBLISHED",
    )
    db_session.add(listing)
    db_session.commit()
    return listing.id


def _finish_migration_agreement(
    client, db_session: Session, admin_cookies: dict, renter, application_id: int, target_listing_id: str,
    *, start_date, term_months: int = 6,
) -> int:
    """Continues a PREMISES_CHANGE's already-created, already-approved
    Application through the ordinary leasing pipeline (real compliance/
    pricing for the target listing, not copied from the original) to a fully
    SIGNED agreement. Returns the new agreement_id."""
    r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    offer_id = r.json()["id"]

    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={"monthlyRent": 500, "depositAmount": 500, "startDate": start_date.isoformat(), "termMonths": term_months},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    # The renter's original tenancy is still deliberately live at this point
    # (see _complete_premises_change_if_applicable's docstring) -- the
    # platform's real occupant-overlap check correctly flags that as BLOCK,
    # same as it would for any other double-booking, so accepting requires
    # the same self-declared override_reason a renter would give in real use.
    r = client.post(
        f"/api/users/rentals/offers/{offer_id}/accept",
        json={"overrideReason": "Room/property change migration -- original tenancy ends once this is signed."},
        cookies=auth_user_cookie(renter),
    )
    assert r.status_code == 200, r.text

    _make_agreement_eligible(db_session, target_listing_id)
    r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    agreement_id = r.json()["id"]

    r = client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    deliver_all_disclosures(client, admin_cookies, agreement_id)
    r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
    assert r.status_code == 200, r.text
    r = client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
    assert r.status_code == 200, r.text
    _pay_off_agreement_obligations(client, db_session, admin_cookies, agreement_id)

    agreement = db_session.get(Agreement, agreement_id)
    assert agreement.status == "SIGNED", agreement.status
    return agreement_id


class TestRequestDateChange:
    def test_renter_can_request_a_new_move_in_date(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="1", start_date=date.today() - timedelta(days=5),
        )
        new_start = date.today() + timedelta(days=10)

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": new_start.isoformat(), "reason": "need a bit more time to relocate"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "PENDING"
        assert body["proposedStartDate"] == new_start.isoformat()
        assert body["changeType"] == "DATE_SHIFT"

    def test_cannot_request_on_an_unsigned_agreement(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="bcr-admin-2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="bcr-renter-2@test.com")

        from tests.test_agreement_engine_extensions_2 import _apply_send_accept_add_terms
        from tests.test_renter_offer_agreement_flow import _make_agreement_eligible

        _app_id, offer_id = _apply_send_accept_add_terms(
            client, db_session, admin_cookies, renter, listing_id, start_date=date.today() + timedelta(days=5),
        )
        offer = db_session.get(Offer, offer_id)
        _make_agreement_eligible(db_session, offer.listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=20)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text

    def test_cannot_request_after_already_moved_in(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="3", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(f"/api/occupancy/agreements/{agreement_id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text
        assert "moved in" in r.json()["detail"]

    def test_cannot_have_two_pending_requests_at_once(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="4", start_date=date.today() - timedelta(days=5),
        )
        new_start = date.today() + timedelta(days=10)
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": new_start.isoformat()}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (new_start + timedelta(days=1)).isoformat()}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text

    def test_cannot_request_a_past_date(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="5", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() - timedelta(days=1)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_a_different_renter_cannot_request_on_someone_elses_agreement(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="6", start_date=date.today() - timedelta(days=5),
        )
        other_renter = _make_verified_renter(db_session, email="bcr-other-6@test.com")
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(other_renter),
        )
        assert r.status_code == 403, r.text


class TestApproveDateChange:
    def test_approval_amends_the_agreement_and_requires_re_signature(self, client, db_session: Session):
        original_start = date.today() - timedelta(days=20)
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="7", start_date=original_start,
        )
        new_start = date.today()

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": new_start.isoformat(), "reason": "delay"}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "APPROVED"
        assert body["resultingAmendmentId"] is not None

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status == "AMENDMENT_PENDING"
        offer = db_session.get(Offer, agreement.offer_id)
        db_session.refresh(offer)
        assert offer.terms[-1].start_date == new_start

        # Move-in still requires fresh signatures -- approval alone isn't effective.
        r = client.post(f"/api/occupancy/agreements/{agreement_id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 409, r.text

        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)

        r = client.post(f"/api/occupancy/agreements/{agreement_id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        r = client.get("/api/users/rentals/change-requests", cookies=auth_user_cookie(renter))
        assert r.json()[0]["status"] == "EFFECTIVE", "request must move past APPROVED once re-signed, not stay stuck there"

    def test_regular_admin_cannot_approve_only_super_admin_can(self, client, db_session: Session):
        original_start = date.today() - timedelta(days=20)
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="bcr-admin-8@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="bcr-renter-8@test.com")
        agreement_id = _full_signed_agreement(client, db_session, admin_cookies, renter, listing_id, start_date=original_start)

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": date.today().isoformat()}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        regular_admin = _make_admin(db_session, email="bcr-regular-admin-8@test.com", role="admin")
        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=auth_admin_cookie(regular_admin),
        )
        assert r.status_code == 403, r.text

    def test_decline_notifies_and_does_not_touch_the_agreement(self, client, db_session: Session):
        original_start = date.today() - timedelta(days=5)
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="9", start_date=original_start,
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (original_start + timedelta(days=10)).isoformat()}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/decline",
            json={"decisionNote": "room needed for another confirmed tenant on those dates"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "DECLINED"

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status == "SIGNED"

        # A new request can now be submitted -- the declined one no longer blocks.
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (original_start + timedelta(days=15)).isoformat()}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text


class TestWithdrawAndList:
    def test_renter_can_withdraw_their_own_pending_request(self, client, db_session: Session):
        original_start = date.today() - timedelta(days=5)
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="10", start_date=original_start,
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (original_start + timedelta(days=10)).isoformat()}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/change-requests/{bcr_id}/withdraw", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "WITHDRAWN"

        r = client.get("/api/users/rentals/change-requests", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1
        assert r.json()[0]["status"] == "WITHDRAWN"

    def test_a_different_renter_cannot_withdraw_someone_elses_request(self, client, db_session: Session):
        original_start = date.today() - timedelta(days=5)
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="11", start_date=original_start,
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (original_start + timedelta(days=10)).isoformat()}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        other_renter = _make_verified_renter(db_session, email="bcr-other-11@test.com")
        r = client.post(f"/api/users/rentals/change-requests/{bcr_id}/withdraw", cookies=auth_user_cookie(other_renter))
        assert r.status_code == 403, r.text

    def test_admin_list_is_scoped_to_their_own_listings(self, client, db_session: Session):
        original_start = date.today() - timedelta(days=5)
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="12", start_date=original_start,
        )
        client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (original_start + timedelta(days=10)).isoformat()}, cookies=auth_user_cookie(renter),
        )

        unrelated_admin = _make_admin(db_session, email="bcr-unrelated-admin-12@test.com", role="admin")
        r = client.get("/api/leasing/booking-change-requests", cookies=auth_admin_cookie(unrelated_admin))
        assert r.status_code == 200, r.text
        assert r.json() == []

        r = client.get("/api/leasing/booking-change-requests", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1


class TestRequestExtension:
    def test_renter_can_request_an_extension_once_active(self, client, db_session: Session):
        start = date.today() - timedelta(days=10)
        agreement_id, admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="13", start_date=start,
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/extension-requests",
            json={"additionalTermMonths": 3, "reason": "want to stay longer"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "PENDING"
        assert body["changeType"] == "EXTENSION"
        assert body["additionalTermMonths"] == 3
        assert body["proposedEndDate"] == _add_months(start, 6 + 3).isoformat()

    def test_cannot_request_extension_before_moving_in(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="14", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/extension-requests",
            json={"additionalTermMonths": 2}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text

    def test_additional_months_must_be_at_least_one(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="15", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/extension-requests",
            json={"additionalTermMonths": 0}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 422, r.text


class TestApproveExtension:
    def test_approval_amends_term_months_and_updates_occupancy_once_re_signed(self, client, db_session: Session):
        start = date.today() - timedelta(days=10)
        agreement_id, admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="16", start_date=start,
        )
        agreement = db_session.get(Agreement, agreement_id)
        occupancy = db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
        original_expected_end_date = occupancy.expected_end_date
        assert original_expected_end_date == _add_months(start, 6)

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/extension-requests",
            json={"additionalTermMonths": 4}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"

        db_session.refresh(agreement)
        assert agreement.status == "AMENDMENT_PENDING"
        offer = db_session.get(Offer, agreement.offer_id)
        db_session.refresh(offer)
        assert offer.terms[-1].term_months == 10

        # Occupancy is untouched until the amendment is fully re-signed.
        db_session.refresh(occupancy)
        assert occupancy.expected_end_date == original_expected_end_date

        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)

        db_session.refresh(occupancy)
        assert occupancy.expected_end_date == _add_months(start, 10)

        r = client.get("/api/users/rentals/change-requests", cookies=auth_user_cookie(renter))
        assert r.json()[0]["status"] == "EFFECTIVE"


class TestRequestShortening:
    def test_renter_can_request_a_shortening_before_move_in(self, client, db_session: Session):
        start = date.today() - timedelta(days=5)
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="17", start_date=start,
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/shortening-requests",
            json={"reducedTermMonths": 2, "reason": "plans changed"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "PENDING"
        assert body["changeType"] == "SHORTENING"
        assert body["additionalTermMonths"] == -2
        assert body["proposedEndDate"] == _add_months(start, 6 - 2).isoformat()

    def test_cannot_request_shortening_after_moving_in(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="18", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/shortening-requests",
            json={"reducedTermMonths": 1}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text
        assert "ending your tenancy" in r.json()["detail"]

    def test_cannot_shorten_to_zero_or_negative_term(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="19", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/shortening-requests",
            json={"reducedTermMonths": 6}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_reduced_months_must_be_at_least_one(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="20", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/shortening-requests",
            json={"reducedTermMonths": 0}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 422, r.text


class TestApproveShortening:
    def test_approval_reduces_term_months(self, client, db_session: Session):
        start = date.today() - timedelta(days=5)
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="21", start_date=start,
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/shortening-requests",
            json={"reducedTermMonths": 2}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        offer = db_session.get(Offer, agreement.offer_id)
        db_session.refresh(offer)
        assert offer.terms[-1].term_months == 4

        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)

        r = client.get("/api/users/rentals/change-requests", cookies=auth_user_cookie(renter))
        assert r.json()[0]["status"] == "EFFECTIVE"


class TestRequestPremisesChange:
    def test_renter_can_request_a_room_property_change(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="22", start_date=date.today() - timedelta(days=5),
        )
        target_id = _make_second_listing(db_session, listing_id="L-TARGET-22")

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": target_id, "reason": "prefer an ensuite room"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "PENDING"
        assert body["changeType"] == "PREMISES_CHANGE"
        assert body["targetListingId"] == target_id

    def test_cannot_target_the_same_listing(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="23", start_date=date.today() - timedelta(days=5),
        )
        agreement = db_session.get(Agreement, agreement_id)
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": agreement.offer.listing_id}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_cannot_target_an_unpublished_listing(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="24", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": "L-DOES-NOT-EXIST"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 404, r.text


class TestApprovePremisesChange:
    def test_approval_opens_a_pre_approved_application_without_touching_the_original(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="25", start_date=date.today() - timedelta(days=5),
        )
        target_id = _make_second_listing(db_session, listing_id="L-TARGET-25")

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": target_id}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "APPROVED"
        assert body["resultingApplicationId"] is not None

        application = db_session.get(Application, body["resultingApplicationId"])
        assert application.listing_id == target_id
        assert application.status == "DECIDED"
        assert application.decisions[-1].decision == "APPROVED"

        # The original agreement is completely untouched by approval alone.
        original = db_session.get(Agreement, agreement_id)
        assert original.status == "SIGNED"

    def test_original_occupancy_ends_only_once_the_new_agreement_is_fully_signed(self, client, db_session: Session):
        start = date.today() - timedelta(days=10)
        agreement_id, admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="26", start_date=start,
        )
        original = db_session.get(Agreement, agreement_id)
        original_occupancy_id = db_session.scalar(
            select(Occupancy).where(Occupancy.offer_id == original.offer_id)
        ).id
        target_id = _make_second_listing(db_session, listing_id="L-TARGET-26")

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": target_id}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]
        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        application_id = r.json()["resultingApplicationId"]

        # Still active -- the new agreement isn't signed yet.
        original_occupancy = db_session.get(Occupancy, original_occupancy_id)
        db_session.refresh(original_occupancy)
        assert original_occupancy.status == "ACTIVE"

        _finish_migration_agreement(
            client, db_session, admin_cookies, renter, application_id, target_id, start_date=date.today() - timedelta(days=1),
        )

        db_session.refresh(original_occupancy)
        assert original_occupancy.status == "ENDED"

        r = client.get("/api/users/rentals/change-requests", cookies=auth_user_cookie(renter))
        migration_request = [x for x in r.json() if x["id"] == bcr_id][0]
        assert migration_request["status"] == "EFFECTIVE"

    def test_original_pre_move_in_agreement_is_voided_once_new_agreement_signed(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="27", start_date=date.today() - timedelta(days=5),
        )
        target_id = _make_second_listing(db_session, listing_id="L-TARGET-27")

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": target_id}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]
        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        application_id = r.json()["resultingApplicationId"]

        _finish_migration_agreement(
            client, db_session, admin_cookies, renter, application_id, target_id, start_date=date.today() - timedelta(days=1),
        )

        original = db_session.get(Agreement, agreement_id)
        db_session.refresh(original)
        assert original.status == "VOID"

    def test_a_different_renter_cannot_request_premises_change(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="28", start_date=date.today() - timedelta(days=5),
        )
        target_id = _make_second_listing(db_session, listing_id="L-TARGET-28")
        other_renter = _make_verified_renter(db_session, email="bcr-other-28@test.com")

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": target_id}, cookies=auth_user_cookie(other_renter),
        )
        assert r.status_code == 403, r.text


class TestRequestFinancialChange:
    def test_renter_can_request_a_rent_change(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="29", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/financial-change-requests",
            json={"proposedMonthlyRent": 450, "reason": "negotiated a lower rate"}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "PENDING"
        assert body["changeType"] == "FINANCIAL_CHANGE"
        assert body["originalMonthlyRent"] == 500.0
        assert body["proposedMonthlyRent"] == 450.0

    def test_cannot_propose_the_same_rent(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="30", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/financial-change-requests",
            json={"proposedMonthlyRent": 500}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_proposed_rent_must_be_positive(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="31", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/financial-change-requests",
            json={"proposedMonthlyRent": 0}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 422, r.text

    def test_a_different_renter_cannot_request_financial_change(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="32", start_date=date.today() - timedelta(days=5),
        )
        other_renter = _make_verified_renter(db_session, email="bcr-other-32@test.com")
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/financial-change-requests",
            json={"proposedMonthlyRent": 450}, cookies=auth_user_cookie(other_renter),
        )
        assert r.status_code == 403, r.text


class TestApproveFinancialChange:
    def test_approval_amends_rent_and_requires_re_signature(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="33", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/financial-change-requests",
            json={"proposedMonthlyRent": 450}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "APPROVED"

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status == "AMENDMENT_PENDING"
        offer = db_session.get(Offer, agreement.offer_id)
        db_session.refresh(offer)
        assert offer.terms[-1].monthly_rent == 450.0

        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)

        r = client.get("/api/users/rentals/change-requests", cookies=auth_user_cookie(renter))
        assert r.json()[0]["status"] == "EFFECTIVE"

    def test_second_rent_change_too_soon_is_blocked_by_jurisdiction_policy(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="34", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/financial-change-requests",
            json={"proposedMonthlyRent": 450}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]
        client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=auth_user_cookie(renter))
        client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)

        r = client.get("/api/users/rentals/change-requests", cookies=auth_user_cookie(renter))
        assert r.json()[0]["status"] == "EFFECTIVE"

        # Immediately requesting another rent change on the same agreement --
        # default policy requires 365 days between changes.
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/financial-change-requests",
            json={"proposedMonthlyRent": 400}, cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text
        assert "days between rent changes" in r.json()["detail"]
