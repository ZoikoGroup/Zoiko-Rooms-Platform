"""ZR-ENG-CLR-008 Section 18 Host Review Wireframe: 'Alternative proposal --
Creates a new proposal/version and invalidates prior consent where material
terms change.' The host counter-proposes different terms instead of a flat
approve/decline; the renter then gets a fresh AWAITING_RENTER decision
(accept -> runs the same amendment engine as a normal approval; decline ->
the whole request is withdrawn)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.booking_change_request import BookingChangeRequest
from tests.conftest import auth_user_cookie
from tests.test_booking_change_requests import _make_second_listing, _signed_agreement_before_move_in


def _submit_date_change(client, agreement_id: int, renter, *, days_ahead: int = 10) -> int:
    r = client.post(
        f"/api/users/rentals/agreements/{agreement_id}/change-requests",
        json={"proposedStartDate": (date.today() + timedelta(days=days_ahead)).isoformat()},
        cookies=auth_user_cookie(renter),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestProposeAlternative:
    def test_host_can_propose_a_different_date_and_it_lands_awaiting_renter(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="alt-01", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _submit_date_change(client, agreement_id, renter, days_ahead=10)

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/propose-alternative",
            json={"proposedStartDate": (date.today() + timedelta(days=20)).isoformat(), "decisionNote": "Room not ready until then"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "AWAITING_RENTER"
        assert body["proposedStartDate"] == (date.today() + timedelta(days=20)).isoformat()

    def test_cannot_propose_alternative_for_premises_change(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="alt-02", start_date=date.today() - timedelta(days=5),
        )
        target_id = _make_second_listing(db_session, listing_id="L-TARGET-ALT02")
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/premises-change-requests",
            json={"targetListingId": target_id}, cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/propose-alternative",
            json={"proposedStartDate": (date.today() + timedelta(days=20)).isoformat()},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

    def test_proposing_the_same_date_is_rejected(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="alt-03", start_date=date.today() - timedelta(days=5),
        )
        proposed = date.today() + timedelta(days=10)
        bcr_id = _submit_date_change(client, agreement_id, renter, days_ahead=10)

        r = client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/propose-alternative",
            json={"proposedStartDate": proposed.isoformat()},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text


class TestAcceptAlternative:
    def test_renter_accepting_runs_the_amendment_engine_and_reaches_awaiting_agreement_action(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="alt-04", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _submit_date_change(client, agreement_id, renter, days_ahead=10)
        alt_date = date.today() + timedelta(days=20)
        client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/propose-alternative",
            json={"proposedStartDate": alt_date.isoformat()}, cookies=admin_cookies,
        )

        r = client.post(f"/api/users/rentals/change-requests/{bcr_id}/accept-alternative", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "AWAITING_AGREEMENT_ACTION"
        assert body["resultingAmendmentId"] is not None

    def test_a_different_renter_cannot_accept(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="alt-05", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _submit_date_change(client, agreement_id, renter, days_ahead=10)
        client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/propose-alternative",
            json={"proposedStartDate": (date.today() + timedelta(days=20)).isoformat()}, cookies=admin_cookies,
        )

        from tests.test_room_hold_atomicity import _make_verified_renter
        other_renter = _make_verified_renter(db_session, email="alt-other-05@test.com")
        r = client.post(f"/api/users/rentals/change-requests/{bcr_id}/accept-alternative", cookies=auth_user_cookie(other_renter))
        assert r.status_code == 403, r.text

    def test_cannot_accept_before_a_host_has_proposed_anything(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="alt-06", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _submit_date_change(client, agreement_id, renter, days_ahead=10)

        r = client.post(f"/api/users/rentals/change-requests/{bcr_id}/accept-alternative", cookies=auth_user_cookie(renter))
        assert r.status_code == 409, r.text


class TestDeclineAlternative:
    def test_renter_declining_withdraws_the_whole_request(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="alt-07", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _submit_date_change(client, agreement_id, renter, days_ahead=10)
        client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/propose-alternative",
            json={"proposedStartDate": (date.today() + timedelta(days=20)).isoformat()}, cookies=admin_cookies,
        )

        r = client.post(f"/api/users/rentals/change-requests/{bcr_id}/decline-alternative", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "WITHDRAWN"

        # Terminal -- can't then also accept it.
        r = client.post(f"/api/users/rentals/change-requests/{bcr_id}/accept-alternative", cookies=auth_user_cookie(renter))
        assert r.status_code == 409, r.text


class TestAlternativeProposalHashIntegrity:
    def test_proposal_hash_changes_when_host_counter_proposes(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="alt-08", start_date=date.today() - timedelta(days=5),
        )
        bcr_id = _submit_date_change(client, agreement_id, renter, days_ahead=10)
        original_hash = db_session.get(BookingChangeRequest, bcr_id).proposal_hash

        client.post(
            f"/api/leasing/booking-change-requests/{bcr_id}/propose-alternative",
            json={"proposedStartDate": (date.today() + timedelta(days=20)).isoformat()}, cookies=admin_cookies,
        )
        db_session.expire_all()
        new_hash = db_session.get(BookingChangeRequest, bcr_id).proposal_hash
        assert new_hash != original_hash
