"""Automated-scan outcomes stay reviewable: a super admin can see and
overrule a document the OCR check sent back, repeated scan failures go to a
human instead of bouncing forever, and a duplicate document is never
auto-verified. See crud/identity_verification.py."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.identity_verification import IdentityVerification
from app.models.notification import Notification
from app.models.party import Party
from app.services import document_ocr
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

_PASSPORT_NUMBER = "P1234567"


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "identity_upload_dir", str(tmp_path))


def _fake_scan(monkeypatch, *, number: str | None, confidence: float):
    monkeypatch.setattr(document_ocr, "is_available", lambda: True)
    monkeypatch.setattr(document_ocr, "extract_and_score", lambda _bytes, _type: (number, confidence))


def _make_renter(db: Session, email: str):
    party = Party(party_type="renter", status="active", jurisdiction="England")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    db.commit()
    return user


def _submit(client, user, content: bytes = b"%PDF-1.4fake jpeg", document_type: str = "passport"):
    r = client.post(
        "/api/users/identity-verifications",
        data={"document_type": document_type},
        files={"file": ("id.pdf", content, "application/pdf")},
        cookies=auth_user_cookie(user),
    )
    assert r.status_code == 201, r.text
    return r.json()


def _super_admin(db: Session):
    # get_system_admin (the actor for an automated verification) resolves the seed admin.
    admin = _make_admin(db, email=settings.seed_admin_email, role="super_admin")
    db.commit()
    return admin


class TestScanFlaggedIsReviewable:
    def test_flagged_upload_appears_in_needs_review_and_admin_can_approve(
        self, client, db_session: Session, uploads, monkeypatch,
    ):
        admin = _super_admin(db_session)
        renter = _make_renter(db_session, "scan-flag@test.com")
        _fake_scan(monkeypatch, number=None, confidence=38.0)

        body = _submit(client, renter)
        assert body["status"] == "additional_evidence_required"

        r = client.get("/api/identity-verifications?status=needs_review", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        [row] = r.json()
        assert row["id"] == body["id"]
        assert row["autoFlagged"] is True
        assert row["ocrConfidence"] == 38.0

        admin_alerts = db_session.query(Notification).filter(
            Notification.notification_type == "identity_verification.auto_flagged",
        ).count()
        assert admin_alerts >= 1

        r = client.post(f"/api/identity-verifications/{body['id']}/verify", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "verified"

        r = client.get("/api/identity-verifications?status=needs_review", cookies=auth_admin_cookie(admin))
        assert r.json() == []

    def test_needs_review_shows_only_the_latest_flagged_upload_per_person(
        self, client, db_session: Session, uploads, monkeypatch,
    ):
        admin = _super_admin(db_session)
        renter = _make_renter(db_session, "scan-latest@test.com")
        _fake_scan(monkeypatch, number=None, confidence=30.0)
        _submit(client, renter, b"%PDF-1.4first")
        second = _submit(client, renter, b"%PDF-1.4second")

        r = client.get("/api/identity-verifications?status=needs_review", cookies=auth_admin_cookie(admin))
        assert [row["id"] for row in r.json()] == [second["id"]]

    def test_admin_requested_evidence_is_not_marked_auto_flagged(self, client, db_session: Session, uploads):
        admin = _super_admin(db_session)
        renter = _make_renter(db_session, "admin-request@test.com")
        body = _submit(client, renter)  # no fake scan: the fake bytes aren't an image, so OCR fails open
        assert body["status"] == "pending"

        r = client.post(
            f"/api/identity-verifications/{body['id']}/request-additional-evidence",
            json={"notes": "Upload the photo page"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["autoFlagged"] is False


class TestRepeatedScanFailures:
    def test_third_upload_goes_to_a_reviewer_instead_of_bouncing(
        self, client, db_session: Session, uploads, monkeypatch,
    ):
        _super_admin(db_session)
        renter = _make_renter(db_session, "scan-repeat@test.com")
        _fake_scan(monkeypatch, number=None, confidence=40.0)

        assert _submit(client, renter, b"%PDF-1.4a")["status"] == "additional_evidence_required"
        assert _submit(client, renter, b"%PDF-1.4b")["status"] == "additional_evidence_required"
        third = _submit(client, renter, b"%PDF-1.4c")
        assert third["status"] == "pending"

        record = db_session.get(IdentityVerification, third["id"])
        assert "Sent to a Zoiko reviewer" in record.verifier_notes
        pending_alert = db_session.query(Notification).filter(
            Notification.notification_type == "identity_verification.submitted",
            Notification.related_entity_id == str(third["id"]),
        ).first()
        assert pending_alert is not None
        assert "couldn't confirm it after repeated attempts" in pending_alert.message

    def test_a_passing_scan_still_auto_verifies_after_earlier_failures(
        self, client, db_session: Session, uploads, monkeypatch,
    ):
        _super_admin(db_session)
        renter = _make_renter(db_session, "scan-recover@test.com")
        _fake_scan(monkeypatch, number=None, confidence=40.0)
        _submit(client, renter, b"%PDF-1.4a")
        _submit(client, renter, b"%PDF-1.4b")

        _fake_scan(monkeypatch, number=_PASSPORT_NUMBER, confidence=90.0)
        assert _submit(client, renter, b"%PDF-1.4c")["status"] == "verified"


class TestDuplicateDocument:
    def test_duplicate_document_is_never_auto_verified(self, client, db_session: Session, uploads, monkeypatch):
        admin = _super_admin(db_session)
        first_renter = _make_renter(db_session, "dup-first@test.com")
        second_renter = _make_renter(db_session, "dup-second@test.com")
        _fake_scan(monkeypatch, number=_PASSPORT_NUMBER, confidence=95.0)

        same_bytes = b"%PDF-1.4the same document"
        assert _submit(client, first_renter, same_bytes)["status"] == "verified"
        duplicate = _submit(client, second_renter, same_bytes)
        assert duplicate["status"] == "pending"

        r = client.get("/api/identity-verifications?status=needs_review", cookies=auth_admin_cookie(admin))
        assert duplicate["id"] in [row["id"] for row in r.json()]
        alert = db_session.query(Notification).filter(
            Notification.notification_type == "identity_verification.submitted",
            Notification.related_entity_id == str(duplicate["id"]),
        ).first()
        assert "matches a previous submission" in alert.message
