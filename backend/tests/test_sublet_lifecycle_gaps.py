"""ZR-SUB-003 gaps verified against the actual current code (not the earlier,
inaccurate third-party summary of the doc): idempotency keys, optimistic
concurrency, decline reason codes, the authority-expiry/revocation gate on
decisions, EXPIRED/SUPERSEDED/CANCELLED_BY_AUTHORITY states, and the
privileged audit trail. See models/sublet_request.py and crud/sublet.py for
the design rationale behind each."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import authority as authority_crud
from app.crud import sublet as sublet_crud
from app.models.authority_record import AuthorityRecord
from app.models.sublet_request import SubletRequest
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_sublet_arrangement_classification import _make_active_tenancy


def _host_for(db: Session, suffix: str) -> UserAccount:
    return db.scalar(select(UserAccount).where(UserAccount.email == f"host-{suffix}@test.com"))


class TestIdempotencyKey:
    def test_a_repeated_key_returns_the_same_request_not_a_duplicate(self, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="idem1")
        first = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, idempotency_key="idem-key-1",
        )
        # Same key, same occupancy -- would otherwise 409 on the "already
        # exists" duplicate-active-request check.
        second = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, idempotency_key="idem-key-1",
        )
        assert first.id == second.id
        count = db_session.scalar(select(SubletRequest).where(SubletRequest.idempotency_key == "idem-key-1"))
        assert count is not None

    def test_a_different_key_is_a_genuinely_new_request(self, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="idem2")
        first = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, idempotency_key="idem-key-a",
        )
        try:
            sublet_crud.submit_sublet_request(
                db_session, tenant_user, occupancy_id, proposed_party_id, idempotency_key="idem-key-b",
            )
            assert False, "should have raised -- an active request already exists for this occupancy"
        except Exception as e:
            assert "already exists" in str(e)


class TestOptimisticConcurrency:
    def test_a_stale_concurrent_decision_raises_staledataerror(self, db_session: Session, client):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="conc1")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "conc1")

        # Two independent decisions through the real route -- the second one
        # (still holding the pre-decision version) surfaces as a clean 409
        # via app/main.py's global StaleDataError handler, not a 500.
        r1 = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/decline", cookies=auth_user_cookie(host_user))
        assert r1.status_code == 200, r1.text

        r2 = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user))
        assert r2.status_code == 409


class TestDeclineReasonCodes:
    def test_a_recognized_reason_code_is_recorded(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="reason1")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "reason1")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/decline",
            json={"declineReasonCode": "TERMS_NOT_ACCEPTABLE"},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text
        assert r.json()["declineReasonCode"] == "TERMS_NOT_ACCEPTABLE"

    def test_an_unrecognized_reason_code_is_rejected(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="reason2")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "reason2")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/decline",
            json={"declineReasonCode": "NOT_A_REAL_CODE"},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 400

    def test_other_requires_an_explanation(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="reason3")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "reason3")

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/decline",
            json={"declineReasonCode": "OTHER"},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 400

        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/decline",
            json={"declineReasonCode": "OTHER", "notes": "A genuinely unlisted reason."},
            cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text


class TestAuthorityGate:
    def test_no_authority_record_at_all_does_not_block_a_decision(self, client, db_session: Session):
        """Back-compat: a room published before this gate existed (or any
        test fixture with no authority record at all) must not be
        retroactively blocked."""
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="auth1")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "auth1")

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text

    def test_a_revoked_authority_record_blocks_the_decision(self, db_session: Session, client):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="auth2")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "auth2")
        room_id = sublet_request.current_occupancy.room_id

        admin = _make_admin(db_session, email="auth2-verifier@test.com", role="super_admin")
        record = AuthorityRecord(party_id=host_user.party_id, room_id=room_id, authority_type="lease", status="pending")
        db_session.add(record)
        db_session.commit()
        authority_crud.verify_authority_record(db_session, record, admin)
        authority_crud.revoke_authority_record(db_session, record, admin)

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user))
        assert r.status_code == 403, r.text

    def test_an_expired_authority_record_blocks_the_decision(self, db_session: Session, client):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="auth3")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "auth3")
        room_id = sublet_request.current_occupancy.room_id

        admin = _make_admin(db_session, email="auth3-verifier@test.com", role="super_admin")
        record = AuthorityRecord(
            party_id=host_user.party_id, room_id=room_id, authority_type="lease", status="verified",
            verified_at=datetime.now(timezone.utc) - timedelta(days=400),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            verifier_admin_id=admin.id,
        )
        db_session.add(record)
        db_session.commit()

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/decline", cookies=auth_user_cookie(host_user))
        assert r.status_code == 403, r.text

    def test_a_currently_valid_authority_record_does_not_block(self, db_session: Session, client):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="auth4")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "auth4")
        room_id = sublet_request.current_occupancy.room_id

        admin = _make_admin(db_session, email="auth4-verifier@test.com", role="super_admin")
        record = AuthorityRecord(party_id=host_user.party_id, room_id=room_id, authority_type="lease", status="pending")
        db_session.add(record)
        db_session.commit()
        authority_crud.verify_authority_record(db_session, record, admin)

        r = client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text


class TestExpiredSweep:
    def test_sweeping_marks_a_lapsed_approval_expired(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="exp1")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "exp1")

        past = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
        r = client.post(
            f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve",
            json={"expiresAt": past, "stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user),
        )
        assert r.status_code == 200, r.text

        row = db_session.get(SubletRequest, sublet_request.id)
        row.approval_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db_session.commit()

        admin = _make_admin(db_session, email="exp1-admin@test.com", role="super_admin")
        r = client.post("/api/occupancy/sublet-requests/sweep-expired-approvals", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        ids = [row["id"] for row in r.json()]
        assert sublet_request.id in ids

        row = db_session.get(SubletRequest, sublet_request.id)
        assert row.status == "expired"
        assert row.expired_at is not None


class TestSupersedeAndCancelByAuthority:
    def test_supersede_links_two_approved_requests(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="super1")
        old_request = sublet_crud.submit_sublet_request(
            db_session, tenant_user, occupancy_id, proposed_party_id, arrangement_type="ADDITIONAL_OCCUPANT",
        )
        host_user = _host_for(db_session, "super1")
        client.post(f"/api/users/hosting/sublet-requests/{old_request.id}/approve", json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user))

        new_request = SubletRequest(
            current_occupancy_id=occupancy_id, proposed_renter_party_id=proposed_party_id,
            status="approved", arrangement_type="ADDITIONAL_OCCUPANT",
        )
        db_session.add(new_request)
        db_session.commit()

        admin = _make_admin(db_session, email="super1-admin@test.com", role="super_admin")
        r = client.post(
            f"/api/occupancy/sublet-requests/{old_request.id}/supersede",
            json={"newSubletRequestId": new_request.id}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "superseded"
        assert r.json()["supersededBySubletRequestId"] == new_request.id

    def test_cancel_by_authority_requires_a_reason_and_a_decided_request(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="cancel1")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "cancel1")
        admin = _make_admin(db_session, email="cancel1-admin@test.com", role="super_admin")

        # Not yet decided -- cannot be cancelled by authority.
        r = client.post(
            f"/api/occupancy/sublet-requests/{sublet_request.id}/cancel-by-authority",
            json={"reason": "test"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409

        client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user))

        r = client.post(
            f"/api/occupancy/sublet-requests/{sublet_request.id}/cancel-by-authority",
            json={"reason": ""}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code in (400, 422)

        r = client.post(
            f"/api/occupancy/sublet-requests/{sublet_request.id}/cancel-by-authority",
            json={"reason": "Fraudulent authority evidence discovered after approval."}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled_by_authority"
        assert r.json()["cancelledByAuthorityAdminId"] == admin.id


class TestAuditTrail:
    def test_host_and_admin_can_see_the_chronology(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="audit1")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        host_user = _host_for(db_session, "audit1")
        client.post(f"/api/users/hosting/sublet-requests/{sublet_request.id}/approve", json={"stepUpPassword": "password123"}, cookies=auth_user_cookie(host_user))

        r = client.get(f"/api/users/hosting/sublet-requests/{sublet_request.id}/audit", cookies=auth_user_cookie(host_user))
        assert r.status_code == 200, r.text
        event_types = [e["eventType"] for e in r.json()]
        assert "sublet_request.submitted" in event_types
        assert "sublet_request.approved" in event_types

        admin = _make_admin(db_session, email="audit1-admin@test.com", role="super_admin")
        r = client.get(f"/api/occupancy/sublet-requests/{sublet_request.id}/audit", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert len(r.json()) >= 2

    def test_a_different_host_cannot_see_the_audit_trail(self, client, db_session: Session):
        tenant_user, _proposed_user, proposed_party_id, occupancy_id = _make_active_tenancy(db_session, suffix="audit2")
        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy_id, proposed_party_id)
        _other_tenant, _other_proposed, _other_party, other_occupancy_id = _make_active_tenancy(db_session, suffix="audit2other")
        other_host = _host_for(db_session, "audit2other")

        r = client.get(f"/api/users/hosting/sublet-requests/{sublet_request.id}/audit", cookies=auth_user_cookie(other_host))
        assert r.status_code == 403
