"""ZR-ENG-CLR-001 Section 1, Rule 2 (5.1) / Section 6.2: the two listing
review outcomes that were previously declared-but-unimplemented --
CHANGES_REQUESTED (a review decision short of full rejection) and
QUARANTINED (an admin pulling a live listing because a new disclosure made
it unsafe/non-compliant, pending re-review).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie
from tests.test_listing_workflow import _create_and_submit_listing


class TestRequestChanges:
    def test_admin_can_request_changes_on_a_listing_pending_review(self, client, db_session: Session):
        listing_id, user_cookies = _create_and_submit_listing(client, db_session, email="cr-host1@test.com")
        admin = _make_admin(db_session, email="cr-admin1@test.com")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/listings/{listing_id}/request-changes", json={"reason": "Please add more photos"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "CHANGES_REQUESTED"
        assert r.json()["rejectionReason"] == "Please add more photos"

        # The host can resubmit from CHANGES_REQUESTED, same as from REJECTED.
        r = client.post(f"/api/users/hosting/listings/{listing_id}/submit-for-review", cookies=user_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "REVIEW"

    def test_reason_is_required(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="cr-host2@test.com")
        admin = _make_admin(db_session, email="cr-admin2@test.com")
        r = client.post(
            f"/api/listings/{listing_id}/request-changes", json={"reason": ""}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_only_legal_from_review(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="cr-host3@test.com")
        admin = _make_admin(db_session, email="cr-admin3@test.com")
        admin_cookies = auth_admin_cookie(admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=admin_cookies)

        r = client.post(
            f"/api/listings/{listing_id}/request-changes", json={"reason": "too late"}, cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text


class TestQuarantine:
    def test_super_admin_can_quarantine_a_published_listing(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="q-host1@test.com")
        super_admin = _make_admin(db_session, email="q-super1@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)

        r = client.post(
            f"/api/listings/{listing_id}/quarantine", json={"reason": "New fire-safety disclosure pending review"},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "QUARANTINED"
        assert r.json()["suspensionReason"] == "New fire-safety disclosure pending review"

    def test_plain_admin_cannot_quarantine(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="q-host2@test.com")
        super_admin = _make_admin(db_session, email="q-super2@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)

        plain_admin = _make_admin(db_session, email="q-plain2@test.com", role="admin")
        r = client.post(
            f"/api/listings/{listing_id}/quarantine", json={"reason": "x"}, cookies=auth_admin_cookie(plain_admin),
        )
        assert r.status_code == 403, r.text

    def test_reason_is_required(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="q-host3@test.com")
        super_admin = _make_admin(db_session, email="q-super3@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)

        r = client.post(f"/api/listings/{listing_id}/quarantine", json={"reason": ""}, cookies=cookies)
        assert r.status_code == 400, r.text

    def test_only_legal_from_published_or_paused(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="q-host4@test.com")
        super_admin = _make_admin(db_session, email="q-super4@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)
        # Still REVIEW -- never published.
        r = client.post(f"/api/listings/{listing_id}/quarantine", json={"reason": "x"}, cookies=cookies)
        assert r.status_code == 409, r.text

    def test_quarantined_listing_can_be_withdrawn(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="q-host5@test.com")
        super_admin = _make_admin(db_session, email="q-super5@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/quarantine", json={"reason": "x"}, cookies=cookies)

        r = client.post(f"/api/listings/{listing_id}/withdraw", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "WITHDRAWN"


class TestOnlySuperAdminCanLiftQuarantineOrSuspension:
    def test_plain_admin_cannot_republish_a_quarantined_listing(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="lift-host1@test.com")
        super_admin = _make_admin(db_session, email="lift-super1@test.com", role="super_admin")
        super_cookies = auth_admin_cookie(super_admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=super_cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=super_cookies)
        client.post(f"/api/listings/{listing_id}/quarantine", json={"reason": "x"}, cookies=super_cookies)

        plain_admin = _make_admin(db_session, email="lift-plain1@test.com", role="admin")
        r = client.post(f"/api/listings/{listing_id}/publish", cookies=auth_admin_cookie(plain_admin))
        assert r.status_code == 403, r.text

    def test_super_admin_can_republish_a_quarantined_listing(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="lift-host2@test.com")
        super_admin = _make_admin(db_session, email="lift-super2@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/quarantine", json={"reason": "x"}, cookies=cookies)

        r = client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"

    def test_resume_route_works_from_paused(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="resume-host1@test.com")
        super_admin = _make_admin(db_session, email="resume-super1@test.com", role="super_admin")
        cookies = auth_admin_cookie(super_admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=cookies)
        client.post(f"/api/listings/{listing_id}/pause", cookies=cookies)

        r = client.post(f"/api/listings/{listing_id}/resume", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"
        assert r.json()["pausedAt"] is None

    def test_resume_route_rejects_non_paused_listing(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="resume-host2@test.com")
        super_admin = _make_admin(db_session, email="resume-super2@test.com", role="super_admin")
        r = client.post(f"/api/listings/{listing_id}/resume", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 409, r.text

    def test_plain_admin_cannot_republish_a_suspended_listing(self, client, db_session: Session):
        listing_id, _ = _create_and_submit_listing(client, db_session, email="lift-host3@test.com")
        super_admin = _make_admin(db_session, email="lift-super3@test.com", role="super_admin")
        super_cookies = auth_admin_cookie(super_admin)
        client.post(f"/api/listings/{listing_id}/approve", cookies=super_cookies)
        client.post(f"/api/listings/{listing_id}/publish", cookies=super_cookies)
        client.post(f"/api/listings/{listing_id}/suspend", json={"reason": "x"}, cookies=super_cookies)

        plain_admin = _make_admin(db_session, email="lift-plain3@test.com", role="admin")
        r = client.post(f"/api/listings/{listing_id}/publish", cookies=auth_admin_cookie(plain_admin))
        assert r.status_code == 403, r.text
