"""ZR-ENG-CLR-012 Section 14/AC-12..15: Property Compliance Credential
Registry -- structured, room-scoped credentials (never a hard-coded document
list) that jurisdiction_gates_pass re-checks the same way it already checks
authority records and occupancy classification. Also covers Section 24's
source-of-truth rule (the gate reads the credential, never a raw evidence
upload) and the resolver's fail-open posture for a jurisdiction with no
required codes configured."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.property_compliance import (
    get_valid_property_compliance_credential,
    issue_property_compliance_credential,
    revoke_property_compliance_credential,
)
from app.models.listing import Listing
from app.models.market_policy import MarketPolicyPack
from app.models.room import Room
from app.services.eligibility import jurisdiction_gates_pass
from app.services.verification_requirements import resolve_property_compliance_requirements
from tests.conftest import _make_admin
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible
from tests.test_room_hold_atomicity import _make_listing_with_room


def _england_policy_pack(db: Session, *, required_codes: list[str]) -> MarketPolicyPack:
    existing = db.scalar(select(MarketPolicyPack).where(MarketPolicyPack.jurisdiction_code == "England"))
    if existing:
        existing.required_property_compliance_codes = required_codes
        db.commit()
        db.refresh(existing)
        return existing
    pack = MarketPolicyPack(
        jurisdiction_code="England", version=1, effective_from=date.today() - timedelta(days=1),
        required_property_compliance_codes=required_codes,
    )
    db.add(pack)
    db.commit()
    db.refresh(pack)
    return pack


def _eligible_listing(db: Session) -> tuple[Listing, Room]:
    listing_id, room_id = _make_listing_with_room(db)
    _make_agreement_eligible(db, listing_id)
    listing = db.get(Listing, listing_id)
    room = db.get(Room, room_id)
    return listing, room


class TestResolverFailsOpenWithoutRequiredCodes:
    def test_jurisdiction_with_no_policy_pack_has_no_requirement(self, db_session: Session):
        assert resolve_property_compliance_requirements(db_session, "ZZ-NOWHERE") == []

    def test_england_with_no_configured_codes_has_no_requirement(self, db_session: Session):
        _england_policy_pack(db_session, required_codes=[])
        assert resolve_property_compliance_requirements(db_session, "England") == []


class TestPropertyCompliancePolicyPackVersion:
    def test_policy_pack_version_recorded_when_a_pack_exists(self, db_session: Session):
        pack = _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-ppv-admin-01@test.com", role="super_admin")

        credential = issue_property_compliance_credential(
            db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT", jurisdiction_code="England",
        )
        assert credential.policy_pack_version == pack.version

    def test_policy_pack_version_null_without_a_jurisdiction_code(self, db_session: Session):
        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-ppv-admin-02@test.com", role="super_admin")

        credential = issue_property_compliance_credential(
            db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT",
        )
        assert credential.policy_pack_version is None


class TestPropertyComplianceGate:
    def test_jurisdiction_gates_block_when_required_credential_missing(self, db_session: Session):
        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        listing, room = _eligible_listing(db_session)

        reasons = jurisdiction_gates_pass(db_session, room, listing.market_release)
        assert any("GAS_SAFETY_CERT" in r for r in reasons), reasons

    def test_jurisdiction_gates_pass_once_credential_issued(self, db_session: Session):
        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-admin-01@test.com", role="super_admin")

        issue_property_compliance_credential(
            db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT",
            issuer_source="Gas Safe registered engineer", jurisdiction_code="England",
        )

        reasons = jurisdiction_gates_pass(db_session, room, listing.market_release)
        assert not any("GAS_SAFETY_CERT" in r for r in reasons), reasons

    def test_expired_credential_still_blocks(self, db_session: Session):
        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-admin-02@test.com", role="super_admin")

        issue_property_compliance_credential(
            db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        assert get_valid_property_compliance_credential(db_session, room.id, "GAS_SAFETY_CERT") is None
        reasons = jurisdiction_gates_pass(db_session, room, listing.market_release)
        assert any("GAS_SAFETY_CERT" in r for r in reasons), reasons

    def test_revoked_credential_no_longer_satisfies_the_gate(self, db_session: Session):
        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-admin-03@test.com", role="super_admin")

        credential = issue_property_compliance_credential(
            db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT",
        )
        revoke_property_compliance_credential(db_session, credential, admin, reason="Certificate found to be forged")

        assert get_valid_property_compliance_credential(db_session, room.id, "GAS_SAFETY_CERT") is None
        reasons = jurisdiction_gates_pass(db_session, room, listing.market_release)
        assert any("GAS_SAFETY_CERT" in r for r in reasons), reasons

    def test_cannot_revoke_an_already_revoked_credential(self, db_session: Session):
        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-admin-04@test.com", role="super_admin")

        credential = issue_property_compliance_credential(
            db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT",
        )
        revoke_property_compliance_credential(db_session, credential, admin, reason="First revoke")

        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            revoke_property_compliance_credential(db_session, credential, admin, reason="Second revoke")
        assert exc_info.value.status_code == 409


class TestPropertyComplianceAdminRoutes:
    def test_full_issue_list_and_revoke_flow_via_http(self, client, db_session: Session):
        from tests.conftest import auth_admin_cookie

        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-admin-05@test.com", role="super_admin")
        cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/verification/property-compliance-credentials",
            json={"roomId": room.id, "requirementCode": "GAS_SAFETY_CERT", "issuerSource": "Gas Safe engineer #123"},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text
        credential_id = r.json()["id"]
        assert r.json()["status"] == "VALID"

        r = client.get(f"/api/verification/property-compliance-credentials/room/{room.id}", cookies=cookies)
        assert r.status_code == 200, r.text
        assert any(c["id"] == credential_id for c in r.json())

        r = client.post(
            f"/api/verification/property-compliance-credentials/{credential_id}/revoke",
            json={"reason": "Superseded by a new inspection"},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "REVOKED"


def _host_admin_for_room(db: Session, room: Room, *, email: str):
    from app.models.admin_user import AdminUser
    from app.models.membership import Membership
    from app.core.security import hash_password

    host_admin = AdminUser(email=email, full_name="Test Host", role="admin", hashed_password=hash_password("password123"), is_active=True, approval_status="approved")
    db.add(host_admin)
    db.flush()
    db.add(Membership(admin_user_id=host_admin.id, party_id=room.property.owner_party_id, role="provider", status="active"))
    db.commit()
    db.refresh(host_admin)
    return host_admin


class TestDeclaredPropertyComplianceCredential:
    def test_host_can_declare_a_credential_for_their_own_room(self, client, db_session: Session):
        from tests.conftest import auth_admin_cookie

        _listing, room = _eligible_listing(db_session)
        host_admin = _host_admin_for_room(db_session, room, email="pcc-host-01@test.com")

        r = client.post(
            "/api/verification/property-compliance-credentials/declare",
            json={"roomId": room.id, "requirementCode": "GAS_SAFETY_CERT", "evidenceRef": "scan-of-my-cert.pdf"},
            cookies=auth_admin_cookie(host_admin),
        )
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "UNDER_REVIEW"

    def test_host_cannot_declare_for_a_room_they_dont_own(self, client, db_session: Session):
        from tests.conftest import auth_admin_cookie

        _listing, room = _eligible_listing(db_session)
        unrelated_admin = _make_admin(db_session, email="pcc-host-02@test.com", role="admin")

        r = client.post(
            "/api/verification/property-compliance-credentials/declare",
            json={"roomId": room.id, "requirementCode": "GAS_SAFETY_CERT", "evidenceRef": "scan.pdf"},
            cookies=auth_admin_cookie(unrelated_admin),
        )
        assert r.status_code == 403, r.text

    def test_declared_credential_does_not_satisfy_the_gate(self, client, db_session: Session):
        from app.crud.property_compliance import declare_property_compliance_credential

        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        _listing, room = _eligible_listing(db_session)
        host_admin = _host_admin_for_room(db_session, room, email="pcc-host-03@test.com")

        declare_property_compliance_credential(
            db_session, host_admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT", evidence_ref="scan.pdf",
        )
        reasons = jurisdiction_gates_pass(db_session, room, _listing.market_release)
        assert any("GAS_SAFETY_CERT" in r for r in reasons), reasons

    def test_super_admin_verifying_declared_credential_makes_it_satisfy_the_gate(self, client, db_session: Session):
        from app.crud.property_compliance import (
            declare_property_compliance_credential,
            verify_declared_property_compliance_credential,
        )

        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        _listing, room = _eligible_listing(db_session)
        host_admin = _host_admin_for_room(db_session, room, email="pcc-host-04@test.com")
        super_admin = _make_admin(db_session, email="pcc-verifier-01@test.com", role="super_admin")

        credential = declare_property_compliance_credential(
            db_session, host_admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT", evidence_ref="scan.pdf",
        )
        verify_declared_property_compliance_credential(db_session, credential, super_admin, jurisdiction_code="England")

        reasons = jurisdiction_gates_pass(db_session, room, _listing.market_release)
        assert not any("GAS_SAFETY_CERT" in r for r in reasons), reasons

    def test_cannot_verify_an_already_valid_credential_as_if_declared(self, db_session: Session):
        from app.crud.property_compliance import verify_declared_property_compliance_credential

        _listing, room = _eligible_listing(db_session)
        super_admin = _make_admin(db_session, email="pcc-verifier-02@test.com", role="super_admin")
        credential = issue_property_compliance_credential(db_session, super_admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT")

        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            verify_declared_property_compliance_credential(db_session, credential, super_admin)

    def test_plain_admin_cannot_verify_declared_credential_via_http(self, client, db_session: Session):
        from tests.conftest import auth_admin_cookie
        from app.crud.property_compliance import declare_property_compliance_credential

        _listing, room = _eligible_listing(db_session)
        host_admin = _host_admin_for_room(db_session, room, email="pcc-host-05@test.com")
        credential = declare_property_compliance_credential(
            db_session, host_admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT", evidence_ref="scan.pdf",
        )
        plain_admin = _make_admin(db_session, email="pcc-plain-01@test.com", role="admin")

        r = client.post(
            f"/api/verification/property-compliance-credentials/{credential.id}/verify-declared",
            json={}, cookies=auth_admin_cookie(plain_admin),
        )
        assert r.status_code == 403, r.text


class TestSuspendAndResume:
    def test_suspend_then_resume_round_trip(self, client, db_session: Session):
        from tests.conftest import auth_admin_cookie

        _england_policy_pack(db_session, required_codes=["GAS_SAFETY_CERT"])
        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-susp-01@test.com", role="super_admin")
        cookies = auth_admin_cookie(admin)

        r = client.post(
            "/api/verification/property-compliance-credentials",
            json={"roomId": room.id, "requirementCode": "GAS_SAFETY_CERT"}, cookies=cookies,
        )
        credential_id = r.json()["id"]

        r = client.post(
            f"/api/verification/property-compliance-credentials/{credential_id}/suspend",
            json={"reason": "Investigating a tenant complaint"}, cookies=cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUSPENDED"

        reasons = jurisdiction_gates_pass(db_session, room, _listing.market_release)
        assert any("GAS_SAFETY_CERT" in r for r in reasons), reasons

        r = client.post(f"/api/verification/property-compliance-credentials/{credential_id}/resume", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "VALID"

        reasons = jurisdiction_gates_pass(db_session, room, _listing.market_release)
        assert not any("GAS_SAFETY_CERT" in r for r in reasons), reasons

    def test_cannot_suspend_a_non_valid_credential(self, db_session: Session):
        from app.crud.property_compliance import suspend_property_compliance_credential

        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-susp-02@test.com", role="super_admin")
        credential = issue_property_compliance_credential(db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT")
        revoke_property_compliance_credential(db_session, credential, admin, reason="test")

        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            suspend_property_compliance_credential(db_session, credential, admin, reason="test")

    def test_cannot_resume_a_non_suspended_credential(self, db_session: Session):
        from app.crud.property_compliance import resume_property_compliance_credential

        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-susp-03@test.com", role="super_admin")
        credential = issue_property_compliance_credential(db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT")

        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            resume_property_compliance_credential(db_session, credential, admin)


class TestComputedExpiringDisplay:
    def test_valid_credential_within_threshold_shows_expiring(self, client, db_session: Session):
        from datetime import datetime, timedelta, timezone
        from app.crud.property_compliance import to_property_compliance_credential_read

        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-exp-01@test.com", role="super_admin")
        credential = issue_property_compliance_credential(
            db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT",
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )

        read = to_property_compliance_credential_read(db_session, credential)
        assert read.status == "EXPIRING"
        # Stored column is untouched -- EXPIRING is purely a display computation.
        assert credential.status == "VALID"

    def test_valid_credential_far_from_expiry_shows_valid(self, db_session: Session):
        from datetime import datetime, timedelta, timezone
        from app.crud.property_compliance import to_property_compliance_credential_read

        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-exp-02@test.com", role="super_admin")
        credential = issue_property_compliance_credential(
            db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT",
            expires_at=datetime.now(timezone.utc) + timedelta(days=90),
        )

        read = to_property_compliance_credential_read(db_session, credential)
        assert read.status == "VALID"

    def test_valid_credential_past_expiry_shows_expired(self, db_session: Session):
        from datetime import datetime, timedelta, timezone
        from app.crud.property_compliance import to_property_compliance_credential_read

        _listing, room = _eligible_listing(db_session)
        admin = _make_admin(db_session, email="pcc-exp-03@test.com", role="super_admin")
        credential = issue_property_compliance_credential(
            db_session, admin, room_id=room.id, requirement_code="GAS_SAFETY_CERT",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )

        read = to_property_compliance_credential_read(db_session, credential)
        assert read.status == "EXPIRED"
