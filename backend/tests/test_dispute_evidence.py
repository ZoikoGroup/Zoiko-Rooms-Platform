"""Integration tests for the ZR-ENG-CLR-010 Section 21 evidence service:
app/models/dispute_evidence.py, app/crud/dispute_evidence.py,
app/core/dispute_evidence_uploads.py and the /evidence routes added to
app/api/routes/disputes.py.

Covers: real SHA-256 hashing on upload, disclosure-class filtering (AC-18),
redaction as a new non-destructive row (AC-15), and the legal-hold flag
(AC-17) -- now backed by a first-class, auditable DisputeLegalHold row
(app/models/dispute_legal_hold.py) rather than a bare boolean flip, with
real enforcement: archiving is refused while a hold is active."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties

_PDF_BYTES = b"%PDF-1.4 fake dispute evidence content"
_PDF_BYTES_2 = b"%PDF-1.4 a different redacted version"


@pytest.fixture(autouse=True)
def _isolated_evidence_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "evidence_upload_dir", str(tmp_path))


def _open_deposit_case(client, renter_cookies) -> tuple[int, int]:
    r = client.post(
        "/api/users/rentals/disputes",
        json={"claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
        cookies=renter_cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["claims"][0]["id"]


class TestUploadAndHashing:
    def test_renter_uploads_evidence_with_real_sha256_hash(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ehost1@test.com", renter_email="erenter1@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            data={"note_text": "receipt", "claim_ids": [claim_id]},
            files={"file": ("receipt.pdf", _PDF_BYTES, "application/pdf")},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["sha256Hash"] == hashlib.sha256(_PDF_BYTES).hexdigest()
        assert body["disclosureClass"] == "PARTY_VISIBLE"
        assert body["provenance"] == "USER_UPLOAD"
        assert body["claimIds"] == [claim_id]
        assert body["sizeBytes"] == len(_PDF_BYTES)

    def test_a_note_with_no_file_has_no_downloadable_file(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ehost2@test.com", renter_email="erenter2@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, _claim_id = _open_deposit_case(client, renter_cookies)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            data={"note_text": "Called host, no response"},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        evidence_id = r.json()["id"]

        r = client.get(f"/api/users/rentals/disputes/{case_id}/evidence/{evidence_id}/file", cookies=renter_cookies)
        assert r.status_code == 404, r.text

    def test_upload_with_neither_file_nor_note_is_rejected(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ehost3@test.com", renter_email="erenter3@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, _claim_id = _open_deposit_case(client, renter_cookies)

        r = client.post(f"/api/users/rentals/disputes/{case_id}/evidence", data={}, cookies=renter_cookies)
        assert r.status_code == 400, r.text


class TestDisclosureFiltering:
    def test_renter_never_sees_an_internal_only_admin_note(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ehost4@test.com", renter_email="erenter4@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, _claim_id = _open_deposit_case(client, renter_cookies)

        admin = _make_admin(db_session, email="ev-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/{case_id}/evidence", data={"note_text": "Internal risk note -- do not share"}, cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        internal_id = r.json()["id"]
        assert r.json()["disclosureClass"] == "INTERNAL_ONLY"

        r = client.get(f"/api/users/rentals/disputes/{case_id}/evidence", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert all(item["id"] != internal_id for item in r.json())

        # AC-18: not visible via direct download either, not just filtered from the list.
        r = client.get(f"/api/users/rentals/disputes/{case_id}/evidence/{internal_id}/file", cookies=renter_cookies)
        assert r.status_code == 403, r.text

        r = client.get(f"/api/admin/disputes/{case_id}/evidence", cookies=admin_cookies)
        assert any(item["id"] == internal_id for item in r.json())

    def test_host_cannot_see_or_download_evidence_on_a_case_they_do_not_own(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ehost5@test.com", renter_email="erenter5@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, _claim_id = _open_deposit_case(client, renter_cookies)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            files={"file": ("receipt.pdf", _PDF_BYTES, "application/pdf")},
            cookies=renter_cookies,
        )
        evidence_id = r.json()["id"]

        intruder_host_user = _make_user(db_session, email="intruder-host@test.com")

        r = client.get(f"/api/users/hosting/disputes/{case_id}/evidence/{evidence_id}/file", cookies=auth_user_cookie(intruder_host_user))
        assert r.status_code in (400, 403), r.text


class TestRenterVisibleOnlyHiddenFromHost:
    """Section 6 gap: evidence backing a sensitive/protected-ground claim
    must reach the renter and staff but never the Host on the same case."""

    def test_renter_visible_only_evidence_is_hidden_from_the_host_on_the_same_case(self, client, db_session: Session):
        host, renter, occ = _make_occupancy_with_parties(db_session, host_email="ehost8@test.com", renter_email="erenter8@test.com")
        renter_cookies = auth_user_cookie(renter)
        host_cookies = auth_user_cookie(host)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        case_id = r.json()["id"]

        admin = _make_admin(db_session, email="ev-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/{case_id}/evidence",
            data={"note_text": "Sensitive protected-ground detail", "disclosure_class": "RENTER_VISIBLE_ONLY"},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        sensitive_id = r.json()["id"]
        assert r.json()["disclosureClass"] == "RENTER_VISIBLE_ONLY"

        r = client.get(f"/api/users/rentals/disputes/{case_id}/evidence", cookies=renter_cookies)
        assert r.status_code == 200, r.text
        assert any(item["id"] == sensitive_id for item in r.json())

        r = client.get(f"/api/users/hosting/disputes/{case_id}/evidence", cookies=host_cookies)
        assert r.status_code == 200, r.text
        assert all(item["id"] != sensitive_id for item in r.json())

        r = client.get(f"/api/admin/disputes/{case_id}/evidence", cookies=admin_cookies)
        assert any(item["id"] == sensitive_id for item in r.json())


class TestRedactionAndLegalHold:
    def test_redaction_creates_a_new_row_and_leaves_the_original_untouched(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ehost7@test.com", renter_email="erenter7@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, claim_id = _open_deposit_case(client, renter_cookies)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            data={"claim_ids": [claim_id]},
            files={"file": ("original.pdf", _PDF_BYTES, "application/pdf")},
            cookies=renter_cookies,
        )
        original_id = r.json()["id"]
        original_hash = r.json()["sha256Hash"]

        admin = _make_admin(db_session, email="ev-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/evidence/{original_id}/redact",
            files={"file": ("redacted.pdf", _PDF_BYTES_2, "application/pdf")},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        redaction = r.json()
        assert redaction["redactedOfEvidenceId"] == original_id
        assert redaction["sha256Hash"] == hashlib.sha256(_PDF_BYTES_2).hexdigest()
        assert redaction["claimIds"] == [claim_id]

        r = client.get(f"/api/admin/disputes/{case_id}/evidence", cookies=admin_cookies)
        by_id = {item["id"]: item for item in r.json()}
        assert by_id[original_id]["sha256Hash"] == original_hash  # untouched
        assert by_id[original_id]["redactedOfEvidenceId"] is None

    def test_admin_sets_and_persists_legal_hold(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ehost8@test.com", renter_email="erenter8@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, _claim_id = _open_deposit_case(client, renter_cookies)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            files={"file": ("receipt.pdf", _PDF_BYTES, "application/pdf")},
            cookies=renter_cookies,
        )
        evidence_id = r.json()["id"]
        assert r.json()["legalHold"] is False

        admin = _make_admin(db_session, email="ev-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/legal-hold", json={"hold": True}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["legalHold"] is True

        r = client.get(f"/api/admin/disputes/{case_id}/evidence", cookies=admin_cookies)
        item = next(i for i in r.json() if i["id"] == evidence_id)
        assert item["legalHold"] is True

    def test_legal_hold_is_a_first_class_auditable_object(self, client, db_session: Session):
        _host, renter, _occ = _make_occupancy_with_parties(db_session, host_email="ehost9@test.com", renter_email="erenter9@test.com")
        renter_cookies = auth_user_cookie(renter)
        case_id, _claim_id = _open_deposit_case(client, renter_cookies)

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence",
            files={"file": ("receipt.pdf", _PDF_BYTES, "application/pdf")},
            cookies=renter_cookies,
        )
        evidence_id = r.json()["id"]

        admin = _make_admin(db_session, email="ev-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        # Placing a hold twice is refused -- there is already an active one.
        r = client.post(
            f"/api/admin/disputes/evidence/{evidence_id}/legal-hold",
            json={"hold": True, "reason": "Pending litigation"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/legal-hold", json={"hold": True}, cookies=admin_cookies)
        assert r.status_code == 409, r.text

        # Archiving is refused while a hold is active.
        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/archive", cookies=admin_cookies)
        assert r.status_code == 409, r.text

        r = client.get(f"/api/admin/disputes/{case_id}/legal-holds", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        holds = r.json()
        assert len(holds) == 1
        assert holds[0]["status"] == "ACTIVE"
        assert holds[0]["reason"] == "Pending litigation"
        assert holds[0]["placedByAdminId"] == admin.id
        assert holds[0]["releasedAt"] is None

        # Releasing twice is refused -- there is no longer an active hold.
        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/legal-hold", json={"hold": False}, cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["legalHold"] is False
        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/legal-hold", json={"hold": False}, cookies=admin_cookies)
        assert r.status_code == 409, r.text

        # Now archiving succeeds.
        r = client.post(f"/api/admin/disputes/evidence/{evidence_id}/archive", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        r = client.get(f"/api/admin/disputes/{case_id}/legal-holds", cookies=admin_cookies)
        holds = r.json()
        assert len(holds) == 1
        assert holds[0]["status"] == "RELEASED"
        assert holds[0]["releasedByAdminId"] == admin.id
        assert holds[0]["releasedAt"] is not None
