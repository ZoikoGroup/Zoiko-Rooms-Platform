"""Integration tests for the ZR-ENG-CLR-010 Section 12/20 dispute
specialist RBAC: app/models/admin_user.py's new dispute_role column,
app/services/dispute_rbac.py's assert_dispute_role, and the gates added to
the admin_router routes in app/api/routes/disputes.py.

Covers: an admin with no dispute_role (the default for every admin that
existed before this field, and any admin nobody has specialized) keeps
full, unrestricted dispute access -- this feature is opt-in, not
retroactive; once an admin IS assigned a specific specialization, they can
perform actions in their own lane and are refused (403) for actions outside
it; super_admin always bypasses regardless of dispute_role; the
/admin-users create/update routes validate dispute_role against the
declared taxonomy."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


def _open_platform_fee_case(client, renter_cookies, occupancy_id: int) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occupancy_id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestUnassignedAdminKeepsBlanketAccess:
    def test_a_plain_admin_with_no_dispute_role_can_still_decide_a_claim(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rbachost1@test.com", renter_email="rbacrenter1@test.com")
        _case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        admin = _make_admin(db_session, email="rbac-admin1@test.com", role="admin")
        assert admin.dispute_role is None
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text


class TestAssignedRoleIsRestrictedToItsLane:
    def test_a_finance_admin_can_open_a_hold_but_not_decide_a_claim(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rbachost2@test.com", renter_email="rbacrenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        claim_id = r.json()["claims"][0]["id"]

        finance_admin = _make_admin(db_session, email="rbac-finance2@test.com", role="admin")
        finance_admin.dispute_role = "FINANCE"
        db_session.commit()
        finance_cookies = auth_admin_cookie(finance_admin)

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 50, "authorityBasis": "test"}, cookies=finance_cookies,
        )
        assert r.status_code == 201, r.text

        _case2_id, fee_claim_id = _open_platform_fee_case(client, renter_cookies, occ.id)
        r = client.post(
            f"/api/admin/disputes/claims/{fee_claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=finance_cookies,
        )
        assert r.status_code == 403, r.text

    def test_a_support_admin_cannot_open_a_financial_hold(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rbachost3@test.com", renter_email="rbacrenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        claim_id = r.json()["claims"][0]["id"]

        support_admin = _make_admin(db_session, email="rbac-support3@test.com", role="admin")
        support_admin.dispute_role = "SUPPORT"
        db_session.commit()

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 50, "authorityBasis": "test"}, cookies=auth_admin_cookie(support_admin),
        )
        assert r.status_code == 403, r.text

    def test_a_trust_and_safety_admin_can_moderate_but_not_close_a_case(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rbachost4@test.com", renter_email="rbacrenter4@test.com")
        case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        ts_admin = _make_admin(db_session, email="rbac-ts4@test.com", role="admin")
        ts_admin.dispute_role = "TRUST_AND_SAFETY"
        db_session.commit()
        ts_cookies = auth_admin_cookie(ts_admin)

        r = client.post(f"/api/admin/disputes/{case_id}/messages", json={"body": "internal note"}, cookies=ts_cookies)
        assert r.status_code == 201, r.text
        message_id = r.json()["id"]

        r = client.post(f"/api/admin/disputes/messages/{message_id}/moderate", json={"hidden": True}, cookies=ts_cookies)
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=ts_cookies,
        )
        assert r.status_code == 403, r.text
        r = client.post(f"/api/admin/disputes/{case_id}/close", cookies=ts_cookies)
        assert r.status_code == 403, r.text

    def test_a_dispute_officer_can_decide_and_close_but_not_touch_a_financial_hold(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rbachost5@test.com", renter_email="rbacrenter5@test.com")
        case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        officer = _make_admin(db_session, email="rbac-officer5@test.com", role="admin")
        officer.dispute_role = "DISPUTE_OFFICER"
        db_session.commit()
        officer_cookies = auth_admin_cookie(officer)

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=officer_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(f"/api/admin/disputes/{case_id}/close", cookies=officer_cookies)
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 10, "authorityBasis": "test"}, cookies=officer_cookies,
        )
        assert r.status_code == 403, r.text


class TestSuperAdminBypass:
    def test_super_admin_bypasses_every_dispute_role_gate_regardless_of_dispute_role(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rbachost6@test.com", renter_email="rbacrenter6@test.com")
        case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        super_admin = _make_admin(db_session, email="rbac-super6@test.com", role="super_admin")
        super_admin.dispute_role = "FINANCE"  # even set to an unrelated dispute role
        db_session.commit()

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text
        r = client.post(f"/api/admin/disputes/{case_id}/close", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text

    def test_a_specialized_super_admins_override_is_audited(self, client, db_session: Session):
        """AC-7: "Super Admin cannot bypass authority controls without an
        authorized, audited legal/compliance override path." A super_admin
        who has ALSO been assigned a dispute_role acting outside that
        role's own lane is a genuine override -- it must produce an
        auditable DomainEvent, not just pass silently."""
        from app.models.domain_event import DomainEvent

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rbachost7@test.com", renter_email="rbacrenter7@test.com")
        case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        super_admin = _make_admin(db_session, email="rbac-super7@test.com", role="super_admin")
        super_admin.dispute_role = "FINANCE"
        db_session.commit()

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text

        events = db_session.query(DomainEvent).filter(DomainEvent.event_type == "dispute_rbac.override_used").all()
        assert len(events) == 1
        assert events[0].resource_id == str(super_admin.id)
        assert events[0].payload["adminDisputeRole"] == "FINANCE"
        assert events[0].payload["requiredRoles"] == ["DISPUTE_OFFICER"]

    def test_an_ordinary_super_admin_with_no_dispute_role_produces_no_override_event(self, client, db_session: Session):
        """The common case (no dispute_role assigned) isn't a bypass of
        anything -- no override event should be created."""
        from app.models.domain_event import DomainEvent

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="rbachost8@test.com", renter_email="rbacrenter8@test.com")
        case_id, claim_id = _open_platform_fee_case(client, auth_user_cookie(renter), occ.id)

        super_admin = _make_admin(db_session, email="rbac-super8@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text

        events = db_session.query(DomainEvent).filter(DomainEvent.event_type == "dispute_rbac.override_used").all()
        assert len(events) == 0


class TestAdminUserManagement:
    def test_creating_an_admin_with_an_invalid_dispute_role_is_refused(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="rbac-mgmt-super1@test.com", role="super_admin")
        r = client.post(
            "/api/admin-users",
            json={"email": "new-admin@test.com", "password": "supersecret123", "disputeRole": "NOT_A_REAL_ROLE"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 400, r.text

    def test_creating_and_updating_an_admin_with_a_valid_dispute_role_succeeds(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="rbac-mgmt-super2@test.com", role="super_admin")
        r = client.post(
            "/api/admin-users",
            json={"email": "new-admin2@test.com", "password": "supersecret123", "disputeRole": "SUPPORT"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["disputeRole"] == "SUPPORT"
        admin_id = r.json()["id"]

        r = client.put(
            f"/api/admin-users/{admin_id}", json={"disputeRole": "LEGAL_COMPLIANCE"}, cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["disputeRole"] == "LEGAL_COMPLIANCE"
