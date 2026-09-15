"""ZR-ENG-CLR-008 Section 4: TERM_SHIFT ('Both start and end move').
Reuses the amendment engine exactly like DATE_SHIFT/EXTENSION/SHORTENING,
but proposes startDate and termMonths together in one call -- see
crud/booking_change_requests.py:request_term_shift."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.leasing import Agreement
from tests.conftest import auth_user_cookie
from tests.test_booking_change_requests import _signed_agreement_before_move_in


class TestRequestTermShift:
    def test_renter_can_request_a_new_move_in_date_and_term_together(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="ts-01", start_date=date.today() - timedelta(days=5),
        )
        new_start = date.today() + timedelta(days=10)

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/term-shift-requests",
            json={"proposedStartDate": new_start.isoformat(), "newTermMonths": 9, "reason": "Plans changed"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "AWAITING_HOST"
        assert body["changeType"] == "TERM_SHIFT"
        assert body["proposedStartDate"] == new_start.isoformat()
        assert body["additionalTermMonths"] == 9

    def test_cannot_request_after_already_moved_in(self, client, db_session: Session):
        from tests.test_booking_change_requests import _signed_agreement_active_occupancy

        agreement_id, admin_cookies, renter = _signed_agreement_active_occupancy(
            client, db_session, email_suffix="ts-02", start_date=date.today() - timedelta(days=10),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/term-shift-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat(), "newTermMonths": 9},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 409, r.text

    def test_same_date_and_term_is_rejected(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="ts-03", start_date=date.today() - timedelta(days=5),
        )
        agreement = db_session.get(Agreement, agreement_id)
        current_start = agreement.offer.terms[-1].start_date
        current_term = agreement.offer.terms[-1].term_months

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/term-shift-requests",
            json={"proposedStartDate": current_start.isoformat(), "newTermMonths": current_term},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 400, r.text

    def test_new_term_must_be_at_least_one_month(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="ts-04", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/term-shift-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat(), "newTermMonths": 0},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 422, r.text


class TestApproveTermShift:
    def test_approval_amends_both_start_date_and_term_and_requires_re_signature(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="ts-05", start_date=date.today() - timedelta(days=5),
        )
        new_start = date.today() + timedelta(days=15)

        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/term-shift-requests",
            json={"proposedStartDate": new_start.isoformat(), "newTermMonths": 8},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "AWAITING_AGREEMENT_ACTION"

        agreement = db_session.get(Agreement, agreement_id)
        db_session.refresh(agreement)
        assert agreement.status == "AMENDMENT_PENDING"

        latest_terms = agreement.offer.terms[-1]
        assert latest_terms.start_date == new_start
        assert latest_terms.term_months == 8
