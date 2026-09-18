"""Integration tests for ZR-ENG-CLR-010 AC-39/QA-Q42 optimistic
concurrency on DisputeResolutionHold: the new `version` column
(SQLAlchemy version_id_col) and crud/disputes.py's
_commit_hold_or_stale_conflict wrapper around approve_financial_hold/
release_financial_hold/confirm_release_financial_hold.

Simulates "two admins load the same hold" by binding a second SQLAlchemy
Session to the same underlying test connection as the `db_session`
fixture -- same transaction, independent identity maps, exactly the
in-memory-staleness shape a real second HTTP request's session would have."""

from __future__ import annotations

from sqlalchemy.orm import Session as SASession

from app.crud import disputes as crud
from app.models.dispute import DisputeResolutionHold
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


class TestConcurrentApprove:
    def test_a_stale_second_session_gets_a_clean_409_not_a_raw_orm_error(self, client, db_session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="hcv1host@test.com", renter_email="hcv1renter@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 20000}},
            cookies=renter_cookies,
        )
        claim_id = r.json()["claims"][0]["id"]

        maker = _make_admin(db_session, email="hcv1-maker@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 20000, "authorityBasis": "large deposit hold"},
            cookies=auth_admin_cookie(maker),
        )
        assert r.status_code == 201, r.text
        hold_id = r.json()["id"]
        assert r.json()["status"] == "PROPOSED"
        assert r.json()["version"] == 1

        checker_a = _make_admin(db_session, email="hcv1-checkerA@test.com", role="super_admin")
        checker_b = _make_admin(db_session, email="hcv1-checkerB@test.com", role="super_admin")

        session_a = db_session
        session_b = SASession(bind=db_session.bind)
        try:
            hold_a = session_a.get(DisputeResolutionHold, hold_id)
            hold_b = session_b.get(DisputeResolutionHold, hold_id)  # loaded before session_a's commit below

            updated = crud.approve_financial_hold(session_a, hold_a, checker_a)
            # The winning approval is confirmed here, before session_b's
            # rollback below -- sharing one raw connection between two
            # Sessions (this test's only way to simulate two concurrent
            # admin requests against SQLite) means further use of
            # session_a after session_b rolls back is not reliable, so
            # nothing is asserted on session_a past this point.
            assert updated.status == "ACTIVE"
            assert updated.version == 2
            assert updated.approved_by_admin_id == checker_a.id

            try:
                crud.approve_financial_hold(session_b, hold_b, checker_b)
                assert False, "expected a 409 from the stale second approval"
            except Exception as exc:
                assert getattr(exc, "status_code", None) == 409
                assert "modified by another admin" in exc.detail
        finally:
            session_b.close()


class TestConcurrentRelease:
    def test_a_stale_second_session_cannot_double_confirm_a_release(self, client, db_session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="hcv2host@test.com", renter_email="hcv2renter@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 20000}},
            cookies=renter_cookies,
        )
        claim_id = r.json()["claims"][0]["id"]

        maker = _make_admin(db_session, email="hcv2-maker@test.com", role="super_admin")
        maker_cookies = auth_admin_cookie(maker)
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 20000, "authorityBasis": "large deposit hold"},
            cookies=maker_cookies,
        )
        hold_id = r.json()["id"]
        checker = _make_admin(db_session, email="hcv2-checker@test.com", role="super_admin")
        r = client.post(f"/api/admin/disputes/financial-holds/{hold_id}/approve", cookies=auth_admin_cookie(checker))
        assert r.json()["status"] == "ACTIVE"

        r = client.post(f"/api/admin/disputes/financial-holds/{hold_id}/release", json={}, cookies=maker_cookies)
        assert r.json()["status"] == "RELEASE_PENDING"

        confirmer_a = _make_admin(db_session, email="hcv2-confirmerA@test.com", role="super_admin")
        confirmer_b = _make_admin(db_session, email="hcv2-confirmerB@test.com", role="super_admin")

        session_a = db_session
        session_b = SASession(bind=db_session.bind)
        try:
            hold_a = session_a.get(DisputeResolutionHold, hold_id)
            hold_b = session_b.get(DisputeResolutionHold, hold_id)

            crud.confirm_release_financial_hold(session_a, hold_a, confirmer_a)
            assert hold_a.status == "RELEASED"

            try:
                crud.confirm_release_financial_hold(session_b, hold_b, confirmer_b)
                assert False, "expected a 409 from the stale second confirmation"
            except Exception as exc:
                assert getattr(exc, "status_code", None) == 409
        finally:
            session_b.close()


class TestOrdinarySingleWriterUnaffected:
    def test_ordinary_below_threshold_hold_still_opens_and_releases_in_one_step(self, client, db_session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="hcv3host@test.com", renter_email="hcv3renter@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 100}},
            cookies=renter_cookies,
        )
        claim_id = r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="hcv3-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 50, "authorityBasis": "small hold"}, cookies=admin_cookies,
        )
        assert r.json()["status"] == "ACTIVE"
        assert r.json()["version"] == 1
        hold_id = r.json()["id"]

        r = client.post(f"/api/admin/disputes/financial-holds/{hold_id}/release", json={}, cookies=admin_cookies)
        assert r.json()["status"] == "RELEASED"
