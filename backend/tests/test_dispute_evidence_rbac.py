"""Integration tests for QA-Q40: "Evidence includes protected/sensitive
data; ordinary Support cannot access restricted item." Previously
app/crud/dispute_evidence.py:assert_evidence_downloadable let ANY
authenticated admin bypass disclosure entirely (`if admin is not None:
return`), including PRIVILEGED_RESTRICTED evidence -- this closes that
hole via app/services/dispute_rbac.py:PRIVILEGED_EVIDENCE_DISPUTE_ROLES.

Covers: an ordinary admin (no dispute_role, or SUPPORT/DISPUTE_OFFICER/
FINANCE) is refused download of and never sees PRIVILEGED_RESTRICTED
evidence in the case list or export; a TRUST_AND_SAFETY or LEGAL_COMPLIANCE
admin (or super_admin) can see and download it; non-privileged evidence is
unaffected for every admin."""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties

_PDF_BYTES = b"%PDF-1.4 fake privileged evidence content"


def _open_case_with_privileged_evidence(
    client, db_session: Session, tmp_path, monkeypatch, *, host_email: str, renter_email: str, super_admin_email: str,
):
    from app.core.config import settings
    monkeypatch.setattr(settings, "evidence_upload_dir", str(tmp_path))

    _host, renter, occ = _make_occupancy_with_parties(db_session, host_email=host_email, renter_email=renter_email)
    r = client.post(
        "/api/users/rentals/disputes",
        json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
        cookies=auth_user_cookie(renter),
    )
    case_id = r.json()["id"]

    super_admin = _make_admin(db_session, email=super_admin_email, role="super_admin")
    r = client.post(
        f"/api/admin/disputes/{case_id}/evidence",
        files={"file": ("fraud_report.pdf", _PDF_BYTES, "application/pdf")},
        data={"disclosure_class": "PRIVILEGED_RESTRICTED"},
        cookies=auth_admin_cookie(super_admin),
    )
    assert r.status_code == 201, r.text
    return case_id, r.json()["id"], super_admin


class TestOrdinaryAdminIsRefused:
    def test_an_unassigned_admin_cannot_download_privileged_evidence(self, client, db_session: Session, tmp_path, monkeypatch):
        case_id, evidence_id, _super_admin = _open_case_with_privileged_evidence(
            client, db_session, tmp_path, monkeypatch,
            host_email="erhost1@test.com", renter_email="errenter1@test.com", super_admin_email="er-super1@test.com",
        )
        ordinary_admin = _make_admin(db_session, email="er-ordinary1@test.com", role="admin")
        assert ordinary_admin.dispute_role is None

        r = client.get(f"/api/admin/disputes/evidence/{evidence_id}/file", cookies=auth_admin_cookie(ordinary_admin))
        assert r.status_code == 403, r.text

    def test_a_support_admin_cannot_download_or_list_privileged_evidence(self, client, db_session: Session, tmp_path, monkeypatch):
        case_id, evidence_id, _super_admin = _open_case_with_privileged_evidence(
            client, db_session, tmp_path, monkeypatch,
            host_email="erhost2@test.com", renter_email="errenter2@test.com", super_admin_email="er-super2@test.com",
        )
        support_admin = _make_admin(db_session, email="er-support2@test.com", role="admin")
        support_admin.dispute_role = "SUPPORT"
        db_session.commit()
        support_cookies = auth_admin_cookie(support_admin)

        r = client.get(f"/api/admin/disputes/evidence/{evidence_id}/file", cookies=support_cookies)
        assert r.status_code == 403, r.text

        r = client.get(f"/api/admin/disputes/{case_id}/evidence", cookies=support_cookies)
        assert r.status_code == 200, r.text
        assert all(e["id"] != evidence_id for e in r.json())

        r = client.get(f"/api/admin/disputes/{case_id}/export", cookies=support_cookies)
        assert r.status_code == 200, r.text
        assert all(e["id"] != evidence_id for e in r.json()["evidenceIndex"])

    def test_a_finance_admin_cannot_see_privileged_evidence_either(self, client, db_session: Session, tmp_path, monkeypatch):
        case_id, evidence_id, _super_admin = _open_case_with_privileged_evidence(
            client, db_session, tmp_path, monkeypatch,
            host_email="erhost3@test.com", renter_email="errenter3@test.com", super_admin_email="er-super3@test.com",
        )
        finance_admin = _make_admin(db_session, email="er-finance3@test.com", role="admin")
        finance_admin.dispute_role = "FINANCE"
        db_session.commit()

        r = client.get(f"/api/admin/disputes/{case_id}/evidence", cookies=auth_admin_cookie(finance_admin))
        assert r.status_code == 200, r.text
        assert all(e["id"] != evidence_id for e in r.json())


class TestPrivilegedRolesCanAccess:
    def test_a_trust_and_safety_admin_can_see_and_download_privileged_evidence(self, client, db_session: Session, tmp_path, monkeypatch):
        case_id, evidence_id, _super_admin = _open_case_with_privileged_evidence(
            client, db_session, tmp_path, monkeypatch,
            host_email="erhost4@test.com", renter_email="errenter4@test.com", super_admin_email="er-super4@test.com",
        )
        ts_admin = _make_admin(db_session, email="er-ts4@test.com", role="admin")
        ts_admin.dispute_role = "TRUST_AND_SAFETY"
        db_session.commit()
        ts_cookies = auth_admin_cookie(ts_admin)

        r = client.get(f"/api/admin/disputes/evidence/{evidence_id}/file", cookies=ts_cookies)
        assert r.status_code == 200, r.text

        r = client.get(f"/api/admin/disputes/{case_id}/evidence", cookies=ts_cookies)
        assert r.status_code == 200, r.text
        assert any(e["id"] == evidence_id for e in r.json())

    def test_a_legal_compliance_admin_can_access_privileged_evidence(self, client, db_session: Session, tmp_path, monkeypatch):
        case_id, evidence_id, _super_admin = _open_case_with_privileged_evidence(
            client, db_session, tmp_path, monkeypatch,
            host_email="erhost5@test.com", renter_email="errenter5@test.com", super_admin_email="er-super5@test.com",
        )
        legal_admin = _make_admin(db_session, email="er-legal5@test.com", role="admin")
        legal_admin.dispute_role = "LEGAL_COMPLIANCE"
        db_session.commit()

        r = client.get(f"/api/admin/disputes/evidence/{evidence_id}/file", cookies=auth_admin_cookie(legal_admin))
        assert r.status_code == 200, r.text

    def test_super_admin_can_always_access_privileged_evidence(self, client, db_session: Session, tmp_path, monkeypatch):
        case_id, evidence_id, super_admin = _open_case_with_privileged_evidence(
            client, db_session, tmp_path, monkeypatch,
            host_email="erhost6@test.com", renter_email="errenter6@test.com", super_admin_email="er-super6@test.com",
        )
        r = client.get(f"/api/admin/disputes/evidence/{evidence_id}/file", cookies=auth_admin_cookie(super_admin))
        assert r.status_code == 200, r.text


class TestOrdinaryEvidenceUnaffected:
    def test_a_support_admin_still_sees_ordinary_evidence(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="erhost7@test.com", renter_email="errenter7@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]
        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/evidence", data={"note_text": "an ordinary renter note"}, cookies=auth_user_cookie(renter),
        )
        evidence_id = r.json()["id"]

        support_admin = _make_admin(db_session, email="er-support7@test.com", role="admin")
        support_admin.dispute_role = "SUPPORT"
        db_session.commit()

        r = client.get(f"/api/admin/disputes/{case_id}/evidence", cookies=auth_admin_cookie(support_admin))
        assert r.status_code == 200, r.text
        assert any(e["id"] == evidence_id for e in r.json())

        r = client.get(f"/api/admin/disputes/evidence/{evidence_id}/file", cookies=auth_admin_cookie(support_admin))
        assert r.status_code == 404, r.text  # note_text-only evidence has no file, but access itself is not refused (no 403)
