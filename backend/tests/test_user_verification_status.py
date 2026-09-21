"""ZR-ENG-CLR-012 Section 19: renter-facing verification status summary --
covers the happy path (identity credential issued, occupancy checks listed)
and that a renter with no party/no submissions gets a sane, non-erroring
"not yet done" response rather than a 500."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud import authority as authority_crud
from app.crud import property_verification as property_verification_crud
from app.crud.identity_verification import verify_identity_verification
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_user_cookie
from tests.test_room_hold_atomicity import _make_verified_renter


class TestRenterVerificationStatus:
    def test_status_reflects_identity_credential_once_verified(self, client, db_session: Session):
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        user = _make_user(db_session, email="uvs-renter-01@test.com")
        user.party_id = party.id
        record = IdentityVerification(party_id=party.id, document_type="passport", status="pending")
        db_session.add(record)
        db_session.commit()

        super_admin = _make_admin(db_session, email="uvs-admin-01@test.com", role="super_admin")
        verify_identity_verification(db_session, record, super_admin)

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["identity"]["status"] == "verified"
        assert body["identity"]["expiresAt"] is not None
        assert body["occupancyEligibility"] == []
        # AC-42: purpose, sharing, retention and alternatives all explained.
        assert body["identity"]["sharingScope"]
        assert "Host" in body["identity"]["sharingScope"]
        assert body["identity"]["retentionNote"]
        # Lister, Property & Authority Verification wireframe: this renter
        # hosts no rooms, so both new claims are present but empty -- never
        # inferred from the identity result above.
        assert body["propertyVerification"] == []
        assert body["authorityToList"] == []

    def test_status_before_any_submission_is_not_submitted(self, client, db_session: Session):
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        user = _make_user(db_session, email="uvs-renter-02@test.com")
        user.party_id = party.id
        db_session.commit()

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["identity"]["status"] == "not_submitted"

    def test_bare_verified_status_without_credential_still_reports_verified_from_submission(self, client, db_session: Session):
        """A row hand-set to 'verified' outside verify_identity_verification()
        (e.g. seeded directly in a test/fixture) has no credential -- the
        status summary falls back to the raw submission's own status rather
        than silently reporting 'not_submitted' for a renter who does, in
        fact, have a verified record on file."""
        renter = _make_verified_renter(db_session, email="uvs-renter-03@test.com")

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text
        assert r.json()["identity"]["status"] == "verified"

    def test_unauthenticated_request_is_rejected(self, client):
        r = client.get("/api/users/verification-status")
        assert r.status_code == 401, r.text


class TestPropertyVerificationAndAuthorityToListClaims:
    """Lister, Property & Authority Verification wireframe: these are
    separate claims from identity -- neither is derived from it, and each
    reflects its own model (PropertyVerification / AuthorityRecord),
    per hosted room."""

    def _make_host_with_room(self, db: Session, *, email: str) -> tuple[UserAccount, Room]:
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db.add(party)
        db.flush()
        user = _make_user(db, email=email)
        user.party_id = party.id
        db.flush()
        prop = Property(owner_party_id=party.id, address="1 Status St", city="Bengaluru", status="active")
        db.add(prop)
        db.flush()
        room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db.add(room)
        db.commit()
        return user, room

    def test_host_with_no_submissions_reports_not_submitted_for_both_claims(self, client, db_session: Session):
        user, _room = self._make_host_with_room(db_session, email="uvs-host-none@test.com")

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["propertyVerification"]) == 1
        assert body["propertyVerification"][0]["status"] == "not_submitted"
        assert len(body["authorityToList"]) == 1
        assert body["authorityToList"][0]["status"] == "not_submitted"

    def test_host_with_verified_property_and_authority_reports_verified(self, client, db_session: Session):
        user, room = self._make_host_with_room(db_session, email="uvs-host-verified@test.com")
        super_admin = _make_admin(db_session, email="uvs-host-verified-admin@test.com", role="super_admin")

        property_record = property_verification_crud.declare_property_verification(db_session, user, room, evidence_ref="deed.pdf")
        property_verification_crud.verify_property_verification(db_session, property_record, super_admin)

        authority_record = authority_crud.declare_authority_record(
            db_session, user, room, relationship_type="OWNER", evidence_ref="deed.pdf",
        )
        authority_crud.verify_authority_record(db_session, authority_record, super_admin)

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["propertyVerification"][0]["status"] == "verified"
        assert body["authorityToList"][0]["status"] == "verified"
        # Identity is untouched by either of the above -- still its own, separate claim.
        assert body["identity"]["status"] == "not_submitted"

    # --- Latest-submission fallback: bug fix coverage. Without a currently
    # valid (verified + unexpired) record, the status must reflect the
    # latest submission's own state -- never silently collapse back to
    # "not_submitted" for a room that has, in fact, had evidence submitted. ---

    def test_pending_property_submission_reports_pending(self, client, db_session: Session):
        user, room = self._make_host_with_room(db_session, email="uvs-host-pv-pending@test.com")
        property_verification_crud.declare_property_verification(db_session, user, room, evidence_ref="deed.pdf")

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["propertyVerification"][0]["status"] == "pending"

    def test_rejected_property_submission_reports_rejected(self, client, db_session: Session):
        user, room = self._make_host_with_room(db_session, email="uvs-host-pv-rejected@test.com")
        super_admin = _make_admin(db_session, email="uvs-host-pv-rejected-admin@test.com", role="super_admin")
        record = property_verification_crud.declare_property_verification(db_session, user, room, evidence_ref="deed.pdf")
        property_verification_crud.reject_property_verification(db_session, record, super_admin, notes="Blurry photo")

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["propertyVerification"][0]["status"] == "rejected"

    def test_additional_evidence_required_property_submission_reports_that_status(self, client, db_session: Session):
        user, room = self._make_host_with_room(db_session, email="uvs-host-pv-more-evidence@test.com")
        super_admin = _make_admin(db_session, email="uvs-host-pv-more-evidence-admin@test.com", role="super_admin")
        record = property_verification_crud.declare_property_verification(db_session, user, room, evidence_ref="deed.pdf")
        property_verification_crud.request_additional_property_evidence(db_session, record, super_admin, notes="Need a second doc")

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["propertyVerification"][0]["status"] == "additional_evidence_required"

    def test_revoked_property_submission_reports_revoked(self, client, db_session: Session):
        user, room = self._make_host_with_room(db_session, email="uvs-host-pv-revoked@test.com")
        super_admin = _make_admin(db_session, email="uvs-host-pv-revoked-admin@test.com", role="super_admin")
        record = property_verification_crud.declare_property_verification(db_session, user, room, evidence_ref="deed.pdf")
        property_verification_crud.verify_property_verification(db_session, record, super_admin)
        property_verification_crud.revoke_property_verification(db_session, record, super_admin, reason="Fraud found")

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["propertyVerification"][0]["status"] == "revoked"

    def test_expired_verified_property_submission_reports_expired_not_verified(self, client, db_session: Session):
        """A row's stored status stays 'verified' forever once set -- nothing
        flips it in the background. The status summary must recompute
        'expired' from expires_at rather than trusting the stale stored value."""
        user, room = self._make_host_with_room(db_session, email="uvs-host-pv-expired@test.com")
        super_admin = _make_admin(db_session, email="uvs-host-pv-expired-admin@test.com", role="super_admin")
        record = property_verification_crud.declare_property_verification(db_session, user, room, evidence_ref="deed.pdf")
        property_verification_crud.verify_property_verification(db_session, record, super_admin)
        record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db_session.commit()

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["propertyVerification"][0]["status"] == "expired"

    # --- Authority to list: same fallback shape. authority.py's own,
    # unchanged status vocabulary uses "failed" (not "rejected") for a
    # rejected submission, and has no "additional evidence required" action --
    # so only the states its existing crud functions actually produce are covered. ---

    def test_pending_authority_submission_reports_pending(self, client, db_session: Session):
        user, room = self._make_host_with_room(db_session, email="uvs-host-auth-pending@test.com")
        authority_crud.declare_authority_record(db_session, user, room, relationship_type="AGENT", evidence_ref="agency.pdf")

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["authorityToList"][0]["status"] == "pending"

    def test_rejected_authority_submission_reports_failed(self, client, db_session: Session):
        user, room = self._make_host_with_room(db_session, email="uvs-host-auth-failed@test.com")
        super_admin = _make_admin(db_session, email="uvs-host-auth-failed-admin@test.com", role="super_admin")
        record = authority_crud.declare_authority_record(db_session, user, room, relationship_type="OWNER", evidence_ref="deed.pdf")
        authority_crud.reject_authority_record(db_session, record, super_admin)

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["authorityToList"][0]["status"] == "failed"

    def test_revoked_authority_submission_reports_revoked(self, client, db_session: Session):
        user, room = self._make_host_with_room(db_session, email="uvs-host-auth-revoked@test.com")
        super_admin = _make_admin(db_session, email="uvs-host-auth-revoked-admin@test.com", role="super_admin")
        record = authority_crud.declare_authority_record(db_session, user, room, relationship_type="OWNER", evidence_ref="deed.pdf")
        authority_crud.verify_authority_record(db_session, record, super_admin)
        authority_crud.revoke_authority_record(db_session, record, super_admin)

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["authorityToList"][0]["status"] == "revoked"

    def test_expired_verified_authority_submission_reports_expired_not_verified(self, client, db_session: Session):
        user, room = self._make_host_with_room(db_session, email="uvs-host-auth-expired@test.com")
        super_admin = _make_admin(db_session, email="uvs-host-auth-expired-admin@test.com", role="super_admin")
        record = authority_crud.declare_authority_record(db_session, user, room, relationship_type="OWNER", evidence_ref="deed.pdf")
        authority_crud.verify_authority_record(db_session, record, super_admin)
        record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db_session.commit()

        r = client.get("/api/users/verification-status", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["authorityToList"][0]["status"] == "expired"
