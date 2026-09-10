"""Integration tests for /api/identity-verifications (admin side) --
previously 59% covered. The document download route is already covered by
test_document_access.py; this covers list/create/verify/reject."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from tests.conftest import _make_admin, auth_admin_cookie


class TestListAndCreate:
    def test_plain_admin_sees_only_their_own_partys_records(self, client, db_session: Session):
        admin_a = _make_admin(db_session, email="idvr-a@test.com", role="admin")
        admin_b = _make_admin(db_session, email="idvr-b@test.com", role="admin")

        r = client.post(
            "/api/identity-verifications",
            json={"documentType": "passport", "encryptedReference": "A-REF"},
            cookies=auth_admin_cookie(admin_a),
        )
        assert r.status_code == 201, r.text
        r = client.post(
            "/api/identity-verifications",
            json={"documentType": "passport", "encryptedReference": "B-REF"},
            cookies=auth_admin_cookie(admin_b),
        )
        assert r.status_code == 201, r.text

        r = client.get("/api/identity-verifications", cookies=auth_admin_cookie(admin_a))
        assert r.status_code == 200, r.text
        refs = {row["encryptedReference"] for row in r.json()}
        assert refs == {"A-REF"}

    def test_super_admin_can_filter_by_status(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="idvr-super@test.com", role="super_admin")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        db_session.add(IdentityVerification(party_id=party.id, document_type="passport", status="pending"))
        db_session.add(IdentityVerification(party_id=party.id, document_type="passport", status="verified"))
        db_session.commit()

        r = client.get("/api/identity-verifications?status=verified", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert all(row["status"] == "verified" for row in r.json())
        assert len(r.json()) >= 1

    def test_create_rejects_an_invalid_document_type(self, client, db_session: Session):
        admin = _make_admin(db_session, email="idvr-invalid@test.com", role="admin")
        r = client.post(
            "/api/identity-verifications",
            json={"documentType": "not-real", "encryptedReference": "X"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 400, r.text

    def test_unauthenticated_request_is_rejected(self, client):
        r = client.get("/api/identity-verifications")
        assert r.status_code == 401, r.text


class TestVerifyAndReject:
    def _make_pending(self, db: Session) -> IdentityVerification:
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db.add(party)
        db.flush()
        record = IdentityVerification(party_id=party.id, document_type="passport", status="pending")
        db.add(record)
        db.commit()
        return record

    def test_plain_admin_cannot_verify(self, client, db_session: Session):
        record = self._make_pending(db_session)
        admin = _make_admin(db_session, email="idvr-verify-plain@test.com", role="admin")
        r = client.post(f"/api/identity-verifications/{record.id}/verify", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text

    def test_super_admin_can_verify(self, client, db_session: Session):
        record = self._make_pending(db_session)
        super_admin = _make_admin(db_session, email="idvr-verify-super@test.com", role="super_admin")
        r = client.post(f"/api/identity-verifications/{record.id}/verify", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "verified"

    def test_verify_unknown_id_is_404(self, client, db_session: Session):
        super_admin = _make_admin(db_session, email="idvr-verify-404@test.com", role="super_admin")
        r = client.post("/api/identity-verifications/999999/verify", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 404, r.text

    def test_super_admin_can_reject_with_notes(self, client, db_session: Session):
        record = self._make_pending(db_session)
        super_admin = _make_admin(db_session, email="idvr-reject-super@test.com", role="super_admin")
        r = client.post(
            f"/api/identity-verifications/{record.id}/reject",
            json={"notes": "Document unreadable"},
            cookies=auth_admin_cookie(super_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"
        assert r.json()["verifierNotes"] == "Document unreadable"

    def test_plain_admin_cannot_reject(self, client, db_session: Session):
        record = self._make_pending(db_session)
        admin = _make_admin(db_session, email="idvr-reject-plain@test.com", role="admin")
        r = client.post(f"/api/identity-verifications/{record.id}/reject", cookies=auth_admin_cookie(admin))
        assert r.status_code == 403, r.text
