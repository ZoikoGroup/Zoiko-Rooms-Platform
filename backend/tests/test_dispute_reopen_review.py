"""Integration tests for ZR-ENG-CLR-010 Section 26/AC-30/31/34/35 case
reopen and internal review request: the two new functions added to
app/crud/disputes.py, the reverse claim-state-machine edges added to
app/services/dispute_state_machine.py, and the /reopen and
/request-review routes added to app/api/routes/disputes.py.

Covers: admin reopen of a CLOSED case (case -> IN_PROGRESS, claim ->
EVIDENCE, closed_at/closed_by_admin_id preserved unchanged); reopen refused
on a non-RESOLVED/CLOSED case; a decided A0 claim can be reviewed within
the window (claim -> INTERNAL_REVIEW, case reopens from CLOSED); a late
review request is refused, not silently accepted; decide_claim re-decides
the reviewed claim with zero code changes; review request refused for a
non-A0 or undecided claim."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _open_deposit_case(client, renter_cookies, occupancy_id: int) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


def _open_and_close_platform_fee_case(client, renter_cookies, occupancy_id: int, admin_cookies: dict) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    case_id, claim_id = body["id"], body["claims"][0]["id"]

    r = client.post(
        f"/api/admin/disputes/claims/{claim_id}/decision",
        json={"outcome": "UPHELD", "reasonCode": "fee_waived"},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text

    r = client.post(f"/api/admin/disputes/{case_id}/close", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CLOSED"
    return case_id, claim_id


class TestAdminReopen:
    def test_admin_reopens_a_closed_case_and_named_claim(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rrhost1@test.com", renter_email="rrrenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        admin = _make_admin(db_session, email="rr-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        case_id, claim_id = _open_and_close_platform_fee_case(client, renter_cookies, occ.id, admin_cookies)

        r = client.get(f"/api/admin/disputes/{case_id}", cookies=admin_cookies)
        original_closed_at = r.json()["closedAt"]

        r = client.post(
            f"/api/admin/disputes/{case_id}/reopen",
            json={"grounds": "MATERIAL_NEW_EVIDENCE", "note": "Renter submitted a new receipt", "claimIds": [claim_id]},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "IN_PROGRESS"
        assert body["reopenGrounds"] == "MATERIAL_NEW_EVIDENCE"
        assert body["reopenedAt"] is not None

        r = client.get(f"/api/admin/disputes/{case_id}", cookies=admin_cookies)
        case = r.json()
        # AC-31: the original closure event is untouched, not cleared.
        assert case["closedAt"] == original_closed_at
        assert case["claims"][0]["status"] == "EVIDENCE"

    def test_reopen_refused_on_a_case_that_is_not_resolved_or_closed(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rrhost2@test.com", renter_email="rrrenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, _claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        admin = _make_admin(db_session, email="rr-admin2@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/reopen",
            json={"grounds": "OTHER"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409, r.text


class TestInternalReviewRequest:
    def test_review_request_moves_claim_to_internal_review_and_reopens_closed_case(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rrhost3@test.com", renter_email="rrrenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        admin = _make_admin(db_session, email="rr-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        case_id, claim_id = _open_and_close_platform_fee_case(client, renter_cookies, occ.id, admin_cookies)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims/{claim_id}/request-review",
            json={"reason": "The fee should have been waived entirely"},
            cookies=renter_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "INTERNAL_REVIEW"

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        case = r.json()
        assert case["status"] == "IN_PROGRESS"
        assert case["reopenGrounds"] == "INTERNAL_REVIEW_REQUESTED"
        assert case["reopenedByAdminId"] is None

    def test_decide_claim_re_decides_the_reviewed_claim_with_no_code_changes(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rrhost4@test.com", renter_email="rrrenter4@test.com")
        renter_cookies = auth_user_cookie(renter)
        admin = _make_admin(db_session, email="rr-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        case_id, claim_id = _open_and_close_platform_fee_case(client, renter_cookies, occ.id, admin_cookies)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims/{claim_id}/request-review",
            json={"reason": "Reconsider"},
            cookies=renter_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "PARTLY_UPHELD", "reasonCode": "half_waived_on_review"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "PARTLY_UPHELD"

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        assert r.json()["status"] == "RESOLVED"

    def test_review_request_past_the_seven_day_window_is_refused(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rrhost5@test.com", renter_email="rrrenter5@test.com")
        renter_cookies = auth_user_cookie(renter)
        admin = _make_admin(db_session, email="rr-admin5@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        case_id, claim_id = _open_and_close_platform_fee_case(client, renter_cookies, occ.id, admin_cookies)

        from app.models.dispute import DisputeResolutionClaim
        claim = db_session.get(DisputeResolutionClaim, claim_id)
        claim.decided_at = datetime.now(timezone.utc) - timedelta(days=8)
        # Phase 10: decide_claim also creates a real DisputeDeadline row
        # computed from the *original* decided_at -- backdating the claim
        # alone doesn't backdate that deadline, so it's adjusted here too
        # to simulate a decision that genuinely happened 8 days ago.
        # AC-29 also auto-creates a PARTY_RESPONSE deadline on the same
        # claim at claim-open time -- filter to INTERNAL_REVIEW specifically
        # rather than assuming there's only one deadline for this claim.
        from sqlalchemy import select
        from app.models.dispute_deadline import DisputeDeadline
        deadline = db_session.scalar(
            select(DisputeDeadline).where(DisputeDeadline.claim_id == claim_id, DisputeDeadline.deadline_type == "INTERNAL_REVIEW")
        )
        deadline.due_at = claim.decided_at + timedelta(days=7)
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims/{claim_id}/request-review",
            json={"reason": "Too late but trying anyway"},
            cookies=renter_cookies,
        )
        assert r.status_code == 409, r.text

    def test_review_request_refused_for_a_non_a0_claim(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rrhost6@test.com", renter_email="rrrenter6@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies, occ.id)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims/{claim_id}/request-review",
            json={"reason": "Reconsider the deposit deduction"},
            cookies=renter_cookies,
        )
        assert r.status_code == 409, r.text
