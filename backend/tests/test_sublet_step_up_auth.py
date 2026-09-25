"""ZR-SUB-003 Section 10: 'Apply step-up authentication to approval/decline
actions where risk signals require it.' No MFA provider exists anywhere in
this codebase -- a fresh password re-entry is the real step-up factor,
required only for the one genuine "risk signal" this taxonomy already has:
approving a REPLACING_ARRANGEMENT_TYPES request (ASSIGNMENT_FULL/
REPLACEMENT_OCCUPANT), an irreversible full handover of the tenancy."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import sublet as sublet_crud
from app.models.user_account import UserAccount
from tests.conftest import auth_user_cookie
from tests.test_sublet_arrangement_classification import _make_active_tenancy


def _host_for(db: Session, suffix: str) -> UserAccount:
    return db.scalar(select(UserAccount).where(UserAccount.email == f"host-{suffix}@test.com"))


class TestStepUpAuthOnReplacingArrangements:
    def test_approving_assignment_full_without_a_password_is_rejected(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="stepup1")
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL",
        )
        host_user = _host_for(db_session, "stepup1")

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(host_user))
        assert r.status_code == 401

    def test_approving_with_the_wrong_password_is_rejected(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="stepup2")
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "REPLACEMENT_OCCUPANT",
        )
        host_user = _host_for(db_session, "stepup2")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "totally-wrong-password"}, cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 401

    def test_approving_with_the_correct_password_succeeds(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="stepup3")
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL",
        )
        host_user = _host_for(db_session, "stepup3")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text

    def test_lower_stakes_arrangements_never_require_step_up(self, client, db_session: Session):
        """ADDITIONAL_OCCUPANT/co-tenancy approvals are not the doc's
        "irreversible full handover" risk signal -- no password required."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="stepup4")
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ADDITIONAL_OCCUPANT",
        )
        host_user = _host_for(db_session, "stepup4")

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text

    def test_decline_never_requires_step_up(self, client, db_session: Session):
        """Declining is the safe/no-op direction -- extra friction there
        would only make it harder to say no."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="stepup5")
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL",
        )
        host_user = _host_for(db_session, "stepup5")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/decline",
            json={"declineReasonCode": "TERMS_NOT_ACCEPTABLE"}, cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text

    def test_admin_legal_ops_override_also_requires_step_up(self, client, db_session: Session):
        from tests.conftest import _make_admin, auth_admin_cookie

        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="stepup6")
        sublet_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, "ASSIGNMENT_FULL",
        )
        admin = _make_admin(db_session, email="stepup6-admin@test.com", role="super_admin")

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=auth_admin_cookie(admin))
        assert r.status_code == 401

        r = client.post(
            f"/api/occupancy/sublet-requests/{sublet_request.id}/approve",
            json={"stepUpPassword": "password123"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
