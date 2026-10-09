"""Integration tests for /api/users/identity-verifications (user side) --
previously 66% covered. The document download route is already covered by
test_document_access.py; this covers list/get-by-id and that uploading is gone."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from tests.conftest import _make_user, auth_user_cookie

_PDF_BYTES = b"%PDF-1.4 fake identity document content"


class TestNoUpload:
    def test_documents_can_no_longer_be_uploaded_here(self, client, db_session: Session):
        """Identity is verified only inside Veriff's own capture
        (ZR-IDV-ADR-001) -- there is no upload route to send a document to."""
        user = _make_user(db_session, email="uidv-no-upload@test.com")
        r = client.post(
            "/api/users/identity-verifications",
            data={"document_type": "passport"},
            files={"file": ("doc.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 405, r.text


class TestListAndGetOwnership:
    def test_list_returns_only_the_current_users_records(self, client, db_session: Session):
        party_a = Party(party_type="renter", status="active", jurisdiction="IN")
        party_b = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add_all([party_a, party_b])
        db_session.flush()
        user_a = _make_user(db_session, email="uidv-list-a@test.com")
        user_a.party_id = party_a.id
        user_b = _make_user(db_session, email="uidv-list-b@test.com")
        user_b.party_id = party_b.id
        db_session.add(IdentityVerification(party_id=party_a.id, document_type="passport", status="pending"))
        db_session.add(IdentityVerification(party_id=party_b.id, document_type="passport", status="pending"))
        db_session.commit()

        r = client.get("/api/users/identity-verifications", cookies=auth_user_cookie(user_a))
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1

    def test_get_by_id_404s_for_an_unknown_record(self, client, db_session: Session):
        user = _make_user(db_session, email="uidv-get-404@test.com")
        db_session.commit()
        r = client.get("/api/users/identity-verifications/999999", cookies=auth_user_cookie(user))
        assert r.status_code == 404, r.text

    def test_get_by_id_403s_for_someone_elses_record(self, client, db_session: Session):
        owner_party = Party(party_type="renter", status="active", jurisdiction="IN")
        other_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add_all([owner_party, other_party])
        db_session.flush()
        other = _make_user(db_session, email="uidv-get-other@test.com")
        other.party_id = other_party.id
        record = IdentityVerification(party_id=owner_party.id, document_type="passport", status="pending")
        db_session.add(record)
        db_session.commit()

        r = client.get(f"/api/users/identity-verifications/{record.id}", cookies=auth_user_cookie(other))
        assert r.status_code == 403, r.text

    def test_get_by_id_returns_own_record(self, client, db_session: Session):
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        user = _make_user(db_session, email="uidv-get-own@test.com")
        user.party_id = party.id
        record = IdentityVerification(party_id=party.id, document_type="passport", status="pending")
        db_session.add(record)
        db_session.commit()

        r = client.get(f"/api/users/identity-verifications/{record.id}", cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert r.json()["id"] == record.id
