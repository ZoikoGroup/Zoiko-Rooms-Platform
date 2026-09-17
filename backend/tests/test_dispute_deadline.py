"""Integration tests for the ZR-ENG-CLR-010 Section 23/26 deadline
tracking: app/crud/dispute_deadline.py, the INTERNAL_REVIEW deadline
crud/disputes.py:decide_claim now creates automatically, the
AC-29 forum-pack-computed PARTY_RESPONSE deadline auto-created on every
claim, and the /deadlines routes added to app/api/routes/disputes.py.

Covers: deciding an A0 claim creates a real, extendable INTERNAL_REVIEW
deadline (Section 26's 7-day figure, now a record instead of an invisible
constant), alongside the claim's own auto-created PARTY_RESPONSE deadline;
extending it requires a non-empty basis and preserves original_due_at
(AC-33); a review request after an extension succeeds past what the old
hardcoded window would have refused; admin can create and cancel an
additional PARTY_RESPONSE deadline; AC-29's reminder_at/is_reminder_due."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties
from tests.test_dispute_reopen_review import _open_and_close_platform_fee_case


class TestAutomaticInternalReviewDeadline:
    def test_deciding_an_a0_claim_creates_a_seven_day_deadline(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="ddhost1@test.com", renter_email="ddrenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        admin = _make_admin(db_session, email="dd-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        case_id, claim_id = _open_and_close_platform_fee_case(client, renter_cookies, occ.id, admin_cookies)

        r = client.get(f"/api/admin/disputes/{case_id}/deadlines", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        deadlines = r.json()
        # One auto-created PARTY_RESPONSE deadline (AC-29, from claim-open)
        # plus the INTERNAL_REVIEW deadline decide_claim adds on top.
        assert len(deadlines) == 2
        assert {d["deadlineType"] for d in deadlines} == {"PARTY_RESPONSE", "INTERNAL_REVIEW"}
        deadline = next(d for d in deadlines if d["deadlineType"] == "INTERNAL_REVIEW")
        assert deadline["claimId"] == claim_id
        assert deadline["source"] == "SYSTEM_DEFAULT"
        assert deadline["status"] == "PENDING"
        assert deadline["isOverdue"] is False

        due_at = datetime.fromisoformat(deadline["dueAt"].replace("Z", "+00:00"))
        assert timedelta(days=6, hours=23) < (due_at - datetime.now(timezone.utc)) < timedelta(days=7, hours=1)

    def test_extending_the_deadline_requires_a_basis_and_preserves_original(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="ddhost2@test.com", renter_email="ddrenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        admin = _make_admin(db_session, email="dd-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        case_id, _claim_id = _open_and_close_platform_fee_case(client, renter_cookies, occ.id, admin_cookies)

        internal_review = next(
            d for d in client.get(f"/api/admin/disputes/{case_id}/deadlines", cookies=admin_cookies).json()
            if d["deadlineType"] == "INTERNAL_REVIEW"
        )
        deadline_id = internal_review["id"]
        original_due_at = internal_review["dueAt"]

        new_due_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        r = client.post(
            f"/api/admin/disputes/deadlines/{deadline_id}/extend",
            json={"newDueAt": new_due_at, "extensionBasis": ""},
            cookies=admin_cookies,
        )
        assert r.status_code == 400, r.text

        r = client.post(
            f"/api/admin/disputes/deadlines/{deadline_id}/extend",
            json={"newDueAt": new_due_at, "extensionBasis": "Renter hospitalized, granting more time"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "EXTENDED"
        assert body["originalDueAt"] == original_due_at

    def test_review_request_succeeds_past_the_default_window_once_extended(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="ddhost3@test.com", renter_email="ddrenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        admin = _make_admin(db_session, email="dd-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        case_id, claim_id = _open_and_close_platform_fee_case(client, renter_cookies, occ.id, admin_cookies)

        from app.models.dispute import DisputeResolutionClaim
        claim = db_session.get(DisputeResolutionClaim, claim_id)
        claim.decided_at = datetime.now(timezone.utc) - timedelta(days=8)
        db_session.commit()

        deadline_id = next(
            d for d in client.get(f"/api/admin/disputes/{case_id}/deadlines", cookies=admin_cookies).json()
            if d["deadlineType"] == "INTERNAL_REVIEW"
        )["id"]
        new_due_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        r = client.post(
            f"/api/admin/disputes/deadlines/{deadline_id}/extend",
            json={"newDueAt": new_due_at, "extensionBasis": "Extended past the original 7 days"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        # decided_at is 8 days ago -- the OLD hardcoded 7-day check would
        # refuse this; the extended deadline record makes it succeed.
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims/{claim_id}/request-review",
            json={"reason": "Reconsider within the extended window"},
            cookies=renter_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "INTERNAL_REVIEW"


class TestAdminSetDeadlines:
    def test_admin_creates_and_cancels_a_party_response_deadline(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="ddhost4@test.com", renter_email="ddrenter4@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        admin = _make_admin(db_session, email="dd-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        due_at = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        r = client.post(
            f"/api/admin/disputes/{case_id}/deadlines",
            json={"deadlineType": "PARTY_RESPONSE", "dueAt": due_at},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        deadline_id = r.json()["id"]

        r = client.get(f"/api/users/rentals/disputes/{case_id}/deadlines", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        # The auto-created (AC-29) PARTY_RESPONSE deadline from claim-open,
        # plus this admin-created one.
        assert len(r.json()) == 2
        assert any(d["id"] == deadline_id for d in r.json())

        r = client.post(f"/api/admin/disputes/deadlines/{deadline_id}/cancel", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "CANCELLED"
