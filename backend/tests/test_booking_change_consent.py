"""ZR-ENG-CLR-008 AC-16/AC-17: 'Consent records bind to exact proposal
version/hash' and 'material proposal change invalidates prior consent'.
app/services/booking_change_consent.py computes a hash of a BookingChangeRequest's
own proposed_* fields at submission and re-verifies it immediately before
approval acts on them -- these tests simulate the row being altered in
between (the only way that could realistically happen given nothing in this
codebase else writes to those fields after creation) and confirm approval
refuses rather than silently approving different terms than what the renter
actually consented to."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.booking_change_request import BookingChangeRequest
from app.services.booking_change_consent import compute_proposal_hash
from tests.conftest import auth_user_cookie
from tests.test_booking_change_requests import _signed_agreement_before_move_in


class TestProposalHashCapturedOnSubmission:
    def test_a_new_request_has_a_non_empty_hash_matching_its_own_fields(self, client, db_session: Session):
        agreement_id, _admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="consent-01", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        bcr = db_session.get(BookingChangeRequest, bcr_id)
        assert bcr.proposal_hash
        assert bcr.proposal_hash == compute_proposal_hash(bcr)


class TestTamperedProposalRefusesApproval:
    def test_changing_the_proposed_date_after_submission_blocks_approval(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="consent-02", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        # Simulate the row being altered after the renter's consent was
        # captured -- nothing in this codebase does this today, which is
        # exactly why it's worth a defense-in-depth check rather than an
        # assumption.
        bcr = db_session.get(BookingChangeRequest, bcr_id)
        bcr.proposed_start_date = date.today() + timedelta(days=99)
        db_session.commit()

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 409, r.text
        assert "consent" in r.json()["detail"].lower()

        db_session.refresh(bcr)
        assert bcr.status == "AWAITING_HOST"

    def test_unmodified_proposal_approves_normally(self, client, db_session: Session):
        agreement_id, admin_cookies, renter = _signed_agreement_before_move_in(
            client, db_session, email_suffix="consent-03", start_date=date.today() - timedelta(days=5),
        )
        r = client.post(
            f"/api/users/rentals/agreements/{agreement_id}/change-requests",
            json={"proposedStartDate": (date.today() + timedelta(days=10)).isoformat()},
            cookies=auth_user_cookie(renter),
        )
        bcr_id = r.json()["id"]

        r = client.post(f"/api/leasing/booking-change-requests/{bcr_id}/approve", json={}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "AWAITING_AGREEMENT_ACTION"
