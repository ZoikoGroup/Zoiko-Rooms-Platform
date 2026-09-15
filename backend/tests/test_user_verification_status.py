"""ZR-ENG-CLR-012 Section 19: renter-facing verification status summary --
covers the happy path (identity credential issued, occupancy checks listed)
and that a renter with no party/no submissions gets a sane, non-erroring
"not yet done" response rather than a 500."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud.identity_verification import verify_identity_verification
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
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
