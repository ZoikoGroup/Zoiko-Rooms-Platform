"""Integration tests for ZR-ENG-CLR-010 QA-Q16 evidence deletion:
app/crud/dispute_evidence.py's new request_deletion, gated by
DisputeLegalHold (app/models/dispute_legal_hold.py), and the new
/evidence/{id}/delete routes added to app/api/routes/disputes.py.

Covers: an uploader can delete their own evidence when no legal hold is
active (file bytes and PII-bearing fields are erased, the row itself
survives so claim links/chronology stay intact); a legal hold refuses the
request (409) and records why on the row rather than silently dropping it;
a party cannot request deletion of evidence they didn't upload; an admin
may action a deletion request on any item; deleting twice is refused;
verify/archive/legal-hold/redact all refuse to act on an already-deleted
item."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties

_PDF_BYTES = b"%PDF-1.4 fake deletable evidence content"


class TestGrantedDeletion:
    def test_the_uploader_can_delete_their_own_evidence(self, client, db_session: Session, tmp_path, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "evidence_upload_dir", str(tmp_path))

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="edhost1@test.com", renter_email="edrenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            files={"file": ("receipt.pdf", _PDF_BYTES, "application/pdf")},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        evidence_id = r.json()["id"]
        stored_files_before = list(tmp_path.iterdir())
        assert len(stored_files_before) == 1

        r = client.post(f"/api/users/rentals/disputes/{case_id}/evidence/{evidence_id}/delete", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deletedAt"] is not None
        assert body["deletionRequestedAt"] is not None
        assert body["deletionRefusedReason"] == ""
        assert body["originalFilename"] == ""

        # The file bytes are actually gone from disk.
        assert list(tmp_path.iterdir()) == []

        # The row survives -- still listed, just with content erased.
        r = client.get(f"/api/users/rentals/disputes/{case_id}/evidence", cookies=renter_cookies)
        assert any(e["id"] == evidence_id for e in r.json())

    def test_deleting_twice_is_refused(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="edhost2@test.com", renter_email="edrenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence", data={"note_text": "a plain note"}, cookies=renter_cookies,
        )
        evidence_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/disputes/{case_id}/evidence/{evidence_id}/delete", cookies=renter_cookies)
        assert r.status_code == 200, r.text

        r = client.post(f"/api/users/rentals/disputes/{case_id}/evidence/{evidence_id}/delete", cookies=renter_cookies)
        assert r.status_code == 409, r.text


class TestLegalHoldExemption:
    def test_an_active_legal_hold_refuses_deletion_and_records_why(self, client, db_session: Session, tmp_path, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "evidence_upload_dir", str(tmp_path))

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="edhost3@test.com", renter_email="edrenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            files={"file": ("receipt.pdf", _PDF_BYTES, "application/pdf")},
            cookies=renter_cookies,
        )
        evidence_id = r.json()["id"]

        admin = _make_admin(db_session, email="ed-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/evidence/{evidence_id}/legal-hold",
            json={"hold": True, "reason": "Pending litigation"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(f"/api/users/rentals/disputes/{case_id}/evidence/{evidence_id}/delete", cookies=renter_cookies)
        assert r.status_code == 409, r.text

        # The refusal itself is recorded, not silent.
        r = client.get(f"/api/admin/disputes/{case_id}/evidence", cookies=admin_cookies)
        item = next(i for i in r.json() if i["id"] == evidence_id)
        assert item["deletionRequestedAt"] is not None
        assert item["deletedAt"] is None
        assert "legal hold" in item["deletionRefusedReason"].lower()

        # Releasing the hold allows the request to succeed afterward.
        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/legal-hold", json={"hold": False}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        r = client.post(f"/api/users/rentals/disputes/{case_id}/evidence/{evidence_id}/delete", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["deletedAt"] is not None


class TestOwnershipAndAdminAction:
    def test_a_party_cannot_delete_evidence_they_did_not_upload(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="edhost4@test.com", renter_email="edrenter4@test.com")
        renter_cookies = auth_user_cookie(renter)
        host_cookies = auth_user_cookie(host)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence", data={"note_text": "renter's own note"}, cookies=renter_cookies,
        )
        evidence_id = r.json()["id"]

        r = client.post(f"/api/users/hosting/disputes/{case_id}/evidence/{evidence_id}/delete", cookies=host_cookies)
        assert r.status_code == 403, r.text

    def test_an_admin_can_action_deletion_of_any_evidence_item(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="edhost5@test.com", renter_email="edrenter5@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence", data={"note_text": "renter's own note"}, cookies=renter_cookies,
        )
        evidence_id = r.json()["id"]

        admin = _make_admin(db_session, email="ed-admin5@test.com", role="super_admin")
        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/delete", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["deletedAt"] is not None


class TestDeletedEvidenceIsInert:
    def test_verify_archive_legal_hold_and_redact_all_refuse_an_already_deleted_item(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="edhost6@test.com", renter_email="edrenter6@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence", data={"note_text": "renter's own note"}, cookies=renter_cookies,
        )
        evidence_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/disputes/{case_id}/evidence/{evidence_id}/delete", cookies=renter_cookies)
        assert r.status_code == 200, r.text

        admin = _make_admin(db_session, email="ed-admin6@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/verify", json={"verified": True}, cookies=admin_cookies)
        assert r.status_code == 409, r.text

        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/archive", cookies=admin_cookies)
        assert r.status_code == 409, r.text

        r = client.post(
            f"/api/admin/disputes/evidence/{evidence_id}/legal-hold", json={"hold": True}, cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text

        r = client.post(
            f"/api/admin/disputes/evidence/{evidence_id}/redact",
            files={"file": ("redacted.pdf", _PDF_BYTES, "application/pdf")},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text
