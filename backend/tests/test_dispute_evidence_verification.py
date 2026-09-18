"""Integration tests for the ZR-ENG-CLR-010 Section 22 evidence
verification state machine: app/services/dispute_state_machine.py's new
transition_evidence_verification and crud/dispute_evidence.py's
verify_evidence/archive_evidence.

Covers: a freshly uploaded item starts RECEIVED; admin marks it VERIFIED,
then flips it to UNVERIFIED on further review; archiving moves any
non-archived status to ARCHIVED; a second archive attempt is refused
(409); verification status changes don't affect disclosure-based
visibility (Phase 2's own filtering is untouched)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties

_PDF_BYTES = b"%PDF-1.4 fake verification evidence content"


class TestVerificationLifecycle:
    def test_a_fresh_upload_starts_received_then_can_be_verified_and_reverted(self, client, db_session: Session, tmp_path, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "evidence_upload_dir", str(tmp_path))

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="evhost1@test.com", renter_email="evrenter1@test.com")
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
        assert r.json()["verificationStatus"] == "RECEIVED"

        admin = _make_admin(db_session, email="ev-verify-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/verify", json={"verified": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["verificationStatus"] == "VERIFIED"

        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/verify", json={"verified": False}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["verificationStatus"] == "UNVERIFIED"

    def test_archiving_is_terminal_and_a_second_attempt_is_refused(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="evhost2@test.com", renter_email="evrenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        admin = _make_admin(db_session, email="ev-verify-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(f"/api/admin/disputes/{case_id}/evidence", data={"note_text": "an admin note"}, cookies=admin_cookies)
        evidence_id = r.json()["id"]

        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/archive", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["verificationStatus"] == "ARCHIVED"

        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/archive", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_verification_status_does_not_affect_disclosure_visibility(self, client, db_session: Session, tmp_path, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "evidence_upload_dir", str(tmp_path))

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="evhost3@test.com", renter_email="evrenter3@test.com")
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

        admin = _make_admin(db_session, email="ev-verify-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/archive", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        # PARTY_VISIBLE evidence stays visible to the renter even once ARCHIVED.
        r = client.get(f"/api/users/rentals/disputes/{case_id}/evidence", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert any(e["id"] == evidence_id for e in r.json())
