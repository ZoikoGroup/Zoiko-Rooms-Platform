"""Integration tests for /api/users/identity-verifications (user side) --
previously 66% covered. The document download route is already covered by
test_document_access.py; this covers submit (multipart)/list/get-by-id."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from tests.conftest import _make_user, auth_user_cookie

_PDF_BYTES = b"%PDF-1.4 fake identity document content"


class TestSubmit:
    def test_requires_authentication(self, client):
        r = client.post(
            "/api/users/identity-verifications",
            data={"document_type": "passport"},
            files={"file": ("doc.pdf", _PDF_BYTES, "application/pdf")},
        )
        assert r.status_code == 401, r.text

    def test_submits_a_pending_verification_with_the_uploaded_document(
        self, client, db_session: Session, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(settings, "identity_upload_dir", str(tmp_path))
        user = _make_user(db_session, email="uidv-submit@test.com")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        user.party_id = party.id
        db_session.commit()

        r = client.post(
            "/api/users/identity-verifications",
            data={"document_type": "passport", "document_number": "P987654"},
            files={"file": ("passport.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "pending"
        assert body["documentType"] == "passport"
        assert body["hasDocument"] is True
        assert body["documentOriginalName"] == "passport.pdf"

    def test_rejects_an_invalid_document_type(self, client, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "identity_upload_dir", str(tmp_path))
        user = _make_user(db_session, email="uidv-badtype@test.com")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        user.party_id = party.id
        db_session.commit()

        r = client.post(
            "/api/users/identity-verifications",
            data={"document_type": "not-a-real-type"},
            files={"file": ("doc.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 400, r.text

    def test_rejects_a_file_that_isnt_actually_pdf_jpg_or_png(self, client, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "identity_upload_dir", str(tmp_path))
        user = _make_user(db_session, email="uidv-badfile@test.com")
        party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        user.party_id = party.id
        db_session.commit()

        r = client.post(
            "/api/users/identity-verifications",
            data={"document_type": "passport"},
            files={"file": ("doc.pdf", b"not a real document", "application/pdf")},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 400, r.text

    def test_user_with_no_party_gets_409(self, client, db_session: Session, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "identity_upload_dir", str(tmp_path))
        user = _make_user(db_session, email="uidv-noparty@test.com")
        db_session.commit()
        assert user.party_id is None

        r = client.post(
            "/api/users/identity-verifications",
            data={"document_type": "passport"},
            files={"file": ("doc.pdf", _PDF_BYTES, "application/pdf")},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 409, r.text


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
