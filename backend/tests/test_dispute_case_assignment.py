"""Integration tests for the ZR-ENG-CLR-010 Section 12/19/23 case
assignment/team routing: app/crud/disputes.py's _default_assigned_team/
reassign_case, and the new /assign route + ?team= filter added to
app/api/routes/disputes.py.

Covers: a safety-flagged case auto-assigns to TRUST_AND_SAFETY; an
unresolved-forum (non-safety) claim auto-assigns to LEGAL_COMPLIANCE; an
ordinary claim auto-assigns to DISPUTE_OPERATIONS; an admin can reassign a
case to a team and/or a specific admin; the admin list endpoint filters by
?team=."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


class TestAutoAssignment:
    def test_safety_flagged_case_auto_assigns_to_trust_and_safety(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="cahost1@test.com", renter_email="carenter1@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "ILLEGAL_LOCKOUT", "claimFamily": "SUBLET_OCCUPANCY", "safetyFlag": True}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["assignedTeam"] == "TRUST_AND_SAFETY"

    def test_unresolved_forum_claim_auto_assigns_to_legal_compliance(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="cahost2@test.com", renter_email="carenter2@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DISCRIMINATION", "claimFamily": "PROTECTED_SAFETY"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        # PROTECTED_SAFETY with no safety_flag resolves to A6 (safety-forced
        # code path isn't hit -- DISCRIMINATION isn't in _SAFETY_FORCED_CODES),
        # so authority stays unresolved (LEGAL_REVIEW_REQUIRED, no A6) -- Legal, not Trust & Safety.
        assert r.json()["assignedTeam"] == "LEGAL_COMPLIANCE"

    def test_ordinary_claim_auto_assigns_to_dispute_operations(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="cahost3@test.com", renter_email="carenter3@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["assignedTeam"] == "DISPUTE_OPERATIONS"


class TestReassignment:
    def test_admin_reassigns_team_and_specific_admin(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="cahost4@test.com", renter_email="carenter4@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        admin = _make_admin(db_session, email="ca-admin1@test.com", role="super_admin")
        other_admin = _make_admin(db_session, email="ca-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/admin/disputes/{case_id}/assign",
            json={"team": "FINANCE", "assignedAdminId": other_admin.id},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["assignedTeam"] == "FINANCE"
        assert body["assignedAdminId"] == other_admin.id

    def test_reassign_requires_at_least_one_field(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="cahost5@test.com", renter_email="carenter5@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        admin = _make_admin(db_session, email="ca-admin3@test.com", role="super_admin")
        r = client.post(f"/api/admin/disputes/{case_id}/assign", json={}, cookies=auth_admin_cookie(admin))
        assert r.status_code == 400, r.text


class TestTeamFilter:
    def test_admin_list_filters_by_team(self, client, db_session: Session):
        _host1, renter1, occ1 = _make_occupancy_with_parties(db_session, host_email="cahost6@test.com", renter_email="carenter6@test.com")
        client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ1.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter1),
        )
        _host2, renter2, occ2 = _make_occupancy_with_parties(db_session, host_email="cahost7@test.com", renter_email="carenter7@test.com")
        client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ2.id, "claim": {"claimCode": "ILLEGAL_LOCKOUT", "claimFamily": "SUBLET_OCCUPANCY", "safetyFlag": True}},
            cookies=auth_user_cookie(renter2),
        )

        admin = _make_admin(db_session, email="ca-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.get("/api/admin/disputes?team=TRUST_AND_SAFETY", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert all(c["assignedTeam"] == "TRUST_AND_SAFETY" for c in r.json())
        assert len(r.json()) == 1

        r = client.get("/api/admin/disputes?team=DISPUTE_OPERATIONS", cookies=admin_cookies)
        assert len(r.json()) == 1
