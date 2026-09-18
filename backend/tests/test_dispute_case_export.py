"""Integration tests for the ZR-ENG-CLR-010 Section 21/28 case export
bundle: app/services/dispute_case_export.py and the /export routes added
to app/api/routes/disputes.py.

Covers: a renter's export never contains an INTERNAL_ONLY evidence item or
a financial hold's privileged authority_basis (disclosure discipline
carried over from Phase 2), an admin's export contains both; the
chronology is timestamp-sorted; a bare case with no evidence/settlements/
proceedings still returns a non-empty chronology (case.opened alone)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties

_PDF_BYTES = b"%PDF-1.4 fake export evidence content"


class TestBareCase:
    def test_a_bare_case_still_returns_a_nonempty_sorted_chronology(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="exhost1@test.com", renter_email="exrenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        r = client.get(f"/api/users/rentals/disputes/{case_id}/export", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        body = r.json()
        # Phase 9: opening a case on an occupancy always creates one
        # automatic SYSTEM_RECORD platform-snapshot evidence item -- a
        # "bare" case (no human-uploaded evidence) still isn't literally empty.
        assert len(body["evidenceIndex"]) == 1
        assert body["evidenceIndex"][0]["provenance"] == "SYSTEM_RECORD"
        assert len(body["chronology"]) >= 3  # case.opened + claim.added + evidence.uploaded
        types = [e["eventType"] for e in body["chronology"]]
        assert "case.opened" in types
        assert "claim.added" in types
        assert "evidence.uploaded" in types
        timestamps = [e["timestamp"] for e in body["chronology"]]
        assert timestamps == sorted(timestamps)
        assert "not an AI-generated" in body["note"]


class TestDisclosureInExport:
    def test_renter_export_excludes_internal_evidence_and_privileged_hold_details(self, client, db_session: Session, tmp_path, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "evidence_upload_dir", str(tmp_path))

        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="exhost2@test.com", renter_email="exrenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id, claim_id = r.json()["id"], r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="ex-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            files={"file": ("receipt.pdf", _PDF_BYTES, "application/pdf")},
            cookies=renter_cookies,
        )
        public_evidence_id = r.json()["id"]

        r = client.post(
            f"/api/admin/disputes/{case_id}/evidence", data={"note_text": "Internal risk note"}, cookies=admin_cookies,
        )
        internal_evidence_id = r.json()["id"]

        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/financial-holds",
            json={"amount": 250, "authorityBasis": "Section 2 scheme custody -- privileged basis text"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text

        r = client.get(f"/api/users/rentals/disputes/{case_id}/export", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        renter_body = r.json()
        renter_evidence_ids = {e["id"] for e in renter_body["evidenceIndex"]}
        assert public_evidence_id in renter_evidence_ids
        assert internal_evidence_id not in renter_evidence_ids

        hold_summaries = " ".join(e["summary"] for e in renter_body["chronology"] if e["eventType"] == "financial_hold.opened")
        assert "privileged basis text" not in hold_summaries
        assert "250" in hold_summaries

        r = client.get(f"/api/admin/disputes/{case_id}/export", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        admin_body = r.json()
        admin_evidence_ids = {e["id"] for e in admin_body["evidenceIndex"]}
        assert public_evidence_id in admin_evidence_ids
        assert internal_evidence_id in admin_evidence_ids

        admin_hold_summaries = " ".join(e["summary"] for e in admin_body["chronology"] if e["eventType"] == "financial_hold.opened")
        assert "privileged basis text" in admin_hold_summaries

    def test_host_cannot_export_a_case_they_do_not_own(self, client, db_session: Session):
        from tests.conftest import _make_user
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="exhost3@test.com", renter_email="exrenter3@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        intruder_host = _make_user(db_session, email="intruder-export-host@test.com")
        r = client.get(f"/api/users/hosting/disputes/{case_id}/export", cookies=auth_user_cookie(intruder_host))
        assert r.status_code in (400, 403), r.text
