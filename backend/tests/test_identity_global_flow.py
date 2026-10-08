"""ZR-IDENTITY-001 global identity verification on top of the Veriff-only
flow (ZR-IDV-ADR-001): account-level profile, country packs, the session
API, re-verification, phone handoff and the privacy rules (masked numbers,
safe events, retention, the admin case view). Veriff's own decisions are
covered in test_identity_veriff.py; here they're the way someone becomes
verified."""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import identity_verification as idv_crud
from app.models.domain_event import DomainEvent
from app.models.identity_profile import IdentityRegulatoryPack
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from app.services.identity import policy
from app.services.identity import service as identity_service
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_identity_veriff import API_KEY, SECRET, FakeVeriff, _sign

PASSPORT = "P1234567"
BASE = "/api/users/identity"


@pytest.fixture(autouse=True)
def veriff(monkeypatch):
    monkeypatch.setattr(settings, "veriff_api_key", API_KEY)
    monkeypatch.setattr(settings, "veriff_shared_secret", SECRET)
    fake = FakeVeriff()
    monkeypatch.setattr(httpx, "request", fake)
    return fake


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "identity_upload_dir", str(tmp_path))
    return tmp_path


def _person(db: Session, email: str, name: str = "Asha Rao", jurisdiction: str = "England"):
    party = Party(party_type="renter", status="active", jurisdiction=jurisdiction)
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.full_name = name
    user.party_id = party.id
    db.commit()
    return user


def _details(client, user, *, given="Asha", family="Rao", dob="1990-05-01", country="GB", middle="", password=""):
    return client.put(f"{BASE}/details", json={
        "givenName": given, "middleNames": middle, "familyName": family, "dateOfBirth": dob, "countryCode": country,
        "currentPassword": password,
    }, cookies=auth_user_cookie(user))


def _start(client, user, method="DOCUMENT", key=None, role="OWNER"):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(f"{BASE}/verifications", json={"method": method, "roleContext": role},
                       headers=headers, cookies=auth_user_cookie(user))


def _submit(client, user, session_id, attested=True):
    return client.post(f"{BASE}/verifications/{session_id}/submit", json={"attested": attested},
                       cookies=auth_user_cookie(user))


def _decide(client, db: Session, session_id: int, decision: str = "approved"):
    session = db.get(IdentityVerification, session_id)
    db.refresh(session)
    body = json.dumps({"status": "success", "verification": {
        "id": session.provider_session_id, "attemptId": "att-1", "status": decision, "vendorData": f"zr-idv-{session.id}",
        "person": {"firstName": "Asha", "lastName": "Rao", "dateOfBirth": "1990-05-01"},
        "document": {"number": PASSPORT, "type": "PASSPORT", "country": "GB", "validUntil": "2031-01-01"},
    }}).encode()
    r = client.post("/api/v1/webhooks/veriff/decision", content=body,
                    headers={"x-auth-client": API_KEY, "x-hmac-signature": _sign(body)})
    assert r.status_code == 200, r.text
    db.expire_all()
    return db.get(IdentityVerification, session_id)


def _super_admin(db: Session):
    from app.models.admin_user import AdminUser

    existing = db.query(AdminUser).filter(AdminUser.role == "super_admin").first()
    if existing is not None:
        return existing
    admin = _make_admin(db, email=settings.seed_admin_email, role="super_admin")
    db.commit()
    return admin


def _verified(client, db: Session, email: str, name: str = "Asha Rao"):
    _super_admin(db)
    user = _person(db, email, name)
    assert _details(client, user).status_code == 200
    sid = _start(client, user).json()["id"]
    assert _submit(client, user, sid).status_code == 200
    return user, sid, _decide(client, db, sid)


class TestProfileAndPolicy:
    def test_a_new_person_starts_not_verified_with_their_name_prefilled(self, client, db_session: Session):
        user = _person(db_session, "idg-new@test.com", "Asha Devi Rao")
        r = client.get(BASE, cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "NOT_STARTED"
        assert (body["givenName"], body["middleNames"], body["familyName"]) == ("Asha", "Devi", "Rao")
        assert body["dashboard"]["header"] == "Identity not verified"
        assert body["dashboard"]["primaryAction"] == "Verify identity"
        assert body["countryCode"] == "GB"  # from the England market region

    def test_the_country_pack_drives_documents_and_date_of_birth(self, client, db_session: Session):
        user = _person(db_session, "idg-pack@test.com")
        gb = client.get(f"{BASE}/policy?country=GB", cookies=auth_user_cookie(user)).json()
        india = client.get(f"{BASE}/policy?country=IN", cookies=auth_user_cookie(user)).json()
        other = client.get(f"{BASE}/policy?country=ZZ", cookies=auth_user_cookie(user)).json()
        assert "aadhaar" in india["acceptedDocumentTypes"] and "aadhaar" not in gb["acceptedDocumentTypes"]
        assert gb["dateOfBirthRequired"] is True
        assert other["countryCode"] == "*"
        for pack in (gb, india, other):
            assert (pack["captureMode"], pack["availableMethods"], pack["providerAvailable"]) == (
                "PROVIDER_HOSTED", ["DOCUMENT"], True)
        countries = {c["countryCode"] for c in client.get(f"{BASE}/countries", cookies=auth_user_cookie(user)).json()}
        assert {"GB", "IN", "US"} <= countries and "*" not in countries

    def test_new_packs_use_veriff(self, db_session: Session):
        policy.ensure_default_packs(db_session)
        packs = db_session.query(IdentityRegulatoryPack).filter_by(active=True).all()
        assert packs and all(p.document_provider_code == "veriff" for p in packs)
        assert all("another verification option" not in p.biometric_consent_text for p in packs)

    def test_a_pack_edited_in_the_database_wins(self, client, db_session: Session):
        user = _person(db_session, "idg-pack-edit@test.com")
        policy.ensure_default_packs(db_session)
        gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB").one()
        gb.accepted_document_types = ["passport"]
        db_session.commit()
        assert client.get(f"{BASE}/policy?country=GB", cookies=auth_user_cookie(user)).json()["acceptedDocumentTypes"] == ["passport"]

    def test_a_pack_cannot_be_switched_to_a_non_veriff_provider_or_manual_method(self, db_session: Session):
        policy.ensure_default_packs(db_session)
        gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB", active=True).one()
        with pytest.raises(ValueError):
            policy.new_pack_version(db_session, gb, {"document_provider_code": "zoiko_document_check"})
        with pytest.raises(ValueError):
            policy.new_pack_version(db_session, gb, {"available_methods": ["DOCUMENT", "MANUAL"]})

    def test_details_need_a_name_country_and_where_required_a_date_of_birth(self, client, db_session: Session):
        user = _person(db_session, "idg-details@test.com")
        assert _details(client, user, family="").status_code == 400
        assert _details(client, user, country="").status_code == 400
        assert _details(client, user, dob=None).status_code == 400  # GB requires it
        future = (date.today() + timedelta(days=1)).isoformat()
        assert _details(client, user, dob=future).status_code == 400

    def test_names_keep_diacritics_and_non_latin_scripts(self, client, db_session: Session):
        user = _person(db_session, "idg-unicode@test.com")
        r = _details(client, user, given="Zoë", family="Ñúñez 李")
        assert r.status_code == 200, r.text
        assert (r.json()["givenName"], r.json()["familyName"]) == ("Zoë", "Ñúñez 李")


class TestVerification:
    def test_a_veriff_approval_verifies_the_account(self, client, db_session: Session):
        user, sid, record = _verified(client, db_session, "idg-pass@test.com")
        assert (record.session_state, record.status, record.provider_code) == ("VERIFIED", "verified", "veriff")
        assert record.masked_document_number == "••••4567"
        assert record.match_results["document_authenticity"] == "PASS"
        profile = identity_service.get_profile(db_session, user.party_id)
        assert (profile.state, profile.assurance_level, profile.verified_legal_name) == ("VERIFIED", "IV-1", "Asha Rao")
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id).id == sid
        assert idv_crud.get_valid_identity_credential(db_session, user.party_id) is not None

    def test_identity_is_not_tied_to_the_document_expiry(self, client, db_session: Session):
        user, sid, _ = _verified(client, db_session, "idg-noexpiry@test.com")
        profile = identity_service.get_profile(db_session, user.party_id)
        assert profile.reverification_required_at is None
        assert db_session.get(IdentityVerification, sid).expires_at is None

    def test_submitting_only_opens_veriff_and_never_verifies_by_itself(self, client, db_session: Session):
        user = _person(db_session, "idg-submit@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        body = _submit(client, user, sid).json()
        assert body["state"] == "IN_PROGRESS" and body["captureAvailable"] is True
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None

    def test_under_the_minimum_age_fails_without_opening_veriff(self, client, db_session: Session, veriff):
        user = _person(db_session, "idg-young@test.com")
        _details(client, user, dob=(date.today() - timedelta(days=365 * 15)).isoformat())
        sid = _start(client, user).json()["id"]
        body = _submit(client, user, sid).json()
        assert (body["state"], body["reasonCodes"]) == ("FAILED", ["AGE_REQUIREMENT_NOT_MET"])
        assert not any(call[0] == "POST" for call in veriff.calls)


class TestSessionRules:
    def test_submitting_needs_the_attestation(self, client, db_session: Session):
        user = _person(db_session, "idg-attest@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        assert _submit(client, user, sid, attested=False).status_code == 400

    def test_starting_twice_reuses_the_open_session_and_honours_the_idempotency_key(self, client, db_session: Session):
        user = _person(db_session, "idg-reuse@test.com")
        _details(client, user)
        first = _start(client, user, key="k-1").json()["id"]
        assert _start(client, user, key="k-1").json()["id"] == first
        assert _start(client, user).json()["id"] == first

    def test_only_the_document_method_exists(self, client, db_session: Session):
        user = _person(db_session, "idg-methods@test.com")
        _details(client, user)
        for method in ("MANUAL", "DIGITAL_IDENTITY"):
            assert _start(client, user, method=method).status_code == 400

    def test_a_verified_person_is_never_asked_again(self, client, db_session: Session):
        user, _sid, _ = _verified(client, db_session, "idg-again@test.com")
        assert _start(client, user).status_code == 409

    def test_a_person_cannot_read_someone_elses_session(self, client, db_session: Session):
        owner = _person(db_session, "idg-own-a@test.com")
        other = _person(db_session, "idg-own-b@test.com")
        _details(client, owner)
        sid = _start(client, owner).json()["id"]
        assert client.get(f"{BASE}/verifications/{sid}", cookies=auth_user_cookie(other)).status_code == 403


class TestReverification:
    def test_changing_the_verified_legal_name_needs_the_password(self, client, db_session):
        """Section 9.3 step-up authentication."""
        user, _sid, _ = _verified(client, db_session, "idg-stepup@test.com")
        assert _details(client, user, given="Asha", family="Kumar").status_code == 403
        assert _details(client, user, given="Asha", family="Kumar", password="wrong").status_code == 403
        profile = identity_service.get_profile(db_session, user.party_id)
        db_session.refresh(profile)
        assert (profile.state, profile.family_name) == ("VERIFIED", "Rao")

    def test_changing_the_verified_legal_name_requires_verifying_again(self, client, db_session):
        user, _sid, _ = _verified(client, db_session, "idg-rename@test.com")
        r = _details(client, user, given="Asha", family="Kumar", password="password123")
        assert r.json()["state"] == "REVERIFICATION_REQUIRED"
        assert r.json()["dashboard"]["primaryAction"] == "Verify again"
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None
        assert idv_crud.get_valid_identity_credential(db_session, user.party_id) is None
        # And they verify again with Veriff.
        sid = _start(client, user).json()["id"]
        assert _submit(client, user, sid).json()["captureAvailable"] is True
        assert _decide(client, db_session, sid).session_state == "VERIFIED"

    def test_a_cosmetic_change_does_not(self, client, db_session):
        user, _sid, _ = _verified(client, db_session, "idg-case@test.com")
        assert _details(client, user, given="ASHA", family="rao").json()["state"] == "VERIFIED"

    def test_periodic_renewal_from_the_country_pack(self, client, db_session):
        policy.ensure_default_packs(db_session)
        gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB").one()
        gb.reverification_interval_days = 30
        db_session.commit()
        user, _sid, _ = _verified(client, db_session, "idg-renew@test.com")
        profile = identity_service.get_profile(db_session, user.party_id)
        assert profile.reverification_required_at is not None
        profile.reverification_required_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        assert identity_service.sweep_reverification_due(db_session) == 1
        db_session.refresh(profile)
        assert (profile.state, profile.reason_codes) == ("REVERIFICATION_REQUIRED", ["PERIODIC_RENEWAL"])

    def test_account_recovery_only_where_the_pack_requires_it(self, client, db_session):
        user, _sid, _ = _verified(client, db_session, "idg-recover@test.com")
        identity_service.on_account_recovery(db_session, user)
        assert identity_service.get_profile(db_session, user.party_id).state == "VERIFIED"
        gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB").one()
        gb.reverify_on_account_recovery = True
        db_session.commit()
        identity_service.on_account_recovery(db_session, user)
        assert identity_service.get_profile(db_session, user.party_id).state == "REVERIFICATION_REQUIRED"


class TestPhoneHandoff:
    def test_a_handoff_token_is_single_use_and_bound_to_the_account(self, client, db_session):
        user = _person(db_session, "idg-hand@test.com")
        other = _person(db_session, "idg-hand-other@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        token = client.post(f"{BASE}/verifications/{sid}/resume", cookies=auth_user_cookie(user)).json()["token"]
        assert client.post(f"{BASE}/handoff/claim", json={"token": token}, cookies=auth_user_cookie(other)).status_code == 404
        r = client.post(f"{BASE}/handoff/claim", json={"token": token}, cookies=auth_user_cookie(user))
        assert (r.status_code, r.json()["id"]) == (200, sid)
        assert client.post(f"{BASE}/handoff/claim", json={"token": token}, cookies=auth_user_cookie(user)).status_code == 404

    def test_an_expired_token_is_refused(self, client, db_session):
        user = _person(db_session, "idg-hand-exp@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        token = client.post(f"{BASE}/verifications/{sid}/resume", cookies=auth_user_cookie(user)).json()["token"]
        record = db_session.get(IdentityVerification, sid)
        record.handoff_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        assert client.post(f"{BASE}/handoff/claim", json={"token": token}, cookies=auth_user_cookie(user)).status_code == 410


class TestPrivacy:
    def test_the_full_document_number_is_never_stored_or_returned(self, client, db_session):
        user, sid, record = _verified(client, db_session, "idg-privacy@test.com")
        assert record.encrypted_reference is None and record.document_file_path is None
        admin = _super_admin(db_session)
        listed = client.get("/api/identity-verifications", cookies=auth_admin_cookie(admin)).text
        assert PASSPORT not in listed and "encryptedReference" not in listed
        mine = client.get("/api/users/identity-verifications", cookies=auth_user_cookie(user)).text
        assert PASSPORT not in mine
        assert PASSPORT not in client.get(BASE, cookies=auth_user_cookie(user)).text

    def test_events_carry_no_names_numbers_or_dates_of_birth(self, client, db_session):
        _verified(client, db_session, "idg-events@test.com")
        events = db_session.query(DomainEvent).filter(DomainEvent.event_type.like("IDENTITY_%")).all()
        types = {e.event_type for e in events}
        assert {"IDENTITY_VERIFICATION_STARTED", "IDENTITY_METHOD_SELECTED", "IDENTITY_PROVIDER_RESULT_RECEIVED",
                "IDENTITY_VERIFIED"} <= types
        dump = json.dumps([e.payload for e in events])
        for secret in (PASSPORT, "Asha", "1990-05-01"):
            assert secret not in dump


def _legacy_upload(db: Session, upload_dir, party_id: int, content: bytes, content_type: str, name: str):
    """A document stored before the upload route was removed (purge and the
    admin viewer still handle these)."""
    (upload_dir / name).write_bytes(content)
    record = IdentityVerification(
        party_id=party_id, document_type="passport", status="verified", session_state="VERIFIED",
        document_file_path=name, document_file_content_type=content_type, masked_document_number="••••4567",
        country_code="GB", decided_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    return record


class TestRetention:
    def test_legacy_evidence_past_the_retention_period_is_deleted_but_the_decision_is_kept(self, db_session, uploads):
        user = _person(db_session, "idg-retention@test.com")
        record = _legacy_upload(db_session, uploads, user.party_id, b"%PDF-1.4 x", "application/pdf", "old.pdf")
        assert identity_service.purge_expired_evidence(db_session) == 0  # still inside the period

        record.decided_at = datetime.now(timezone.utc) - timedelta(days=400)
        db_session.commit()
        assert identity_service.purge_expired_evidence(db_session) == 1
        db_session.refresh(record)
        assert record.document_file_path is None and record.evidence_purged_at is not None
        assert not (uploads / "old.pdf").exists()
        assert record.masked_document_number == "••••4567"

    def test_nothing_is_purged_under_a_pack_without_a_retention_period(self, db_session, uploads):
        user = _person(db_session, "idg-retention-none@test.com")
        record = _legacy_upload(db_session, uploads, user.party_id, b"%PDF-1.4 x", "application/pdf", "keep.pdf")
        policy.ensure_default_packs(db_session)
        gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB").one()
        gb.evidence_retention_days = None
        record.decided_at = datetime.now(timezone.utc) - timedelta(days=4000)
        db_session.commit()
        assert identity_service.purge_expired_evidence(db_session) == 0


class TestMetrics:
    def test_metrics_are_aggregates_only(self, client, db_session):
        admin = _super_admin(db_session)
        _verified(client, db_session, "idg-metrics@test.com")
        r = client.get("/api/identity-verifications/metrics", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["verified"] >= 1 and body["startToVerifiedRate"] is not None
        assert "DOCUMENT" in body["byMethod"]
        assert "manualReviewRate" not in body and "pendingReview" not in body
        assert set(body["timeToDecisionHours"]) == {"median", "p90"}
        assert PASSPORT not in r.text and "Asha" not in r.text


class TestSecureEvidenceViewer:
    def test_a_legacy_document_is_shown_inline_never_cached_and_the_view_is_logged(self, client, db_session, uploads):
        from app.models.audit import AuditEvent

        admin = _super_admin(db_session)
        user = _person(db_session, "idg-viewer@test.com")
        record = _legacy_upload(db_session, uploads, user.party_id, b"%PDF-1.4 x", "application/pdf", "view.pdf")
        r = client.get(f"/api/identity-verifications/{record.id}/document", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200
        assert r.headers["content-disposition"] == "inline"
        assert "no-store" in r.headers["cache-control"]
        assert db_session.query(AuditEvent).filter(AuditEvent.action == "identity_verification.evidence_viewed").count() == 1

    def test_images_are_watermarked(self, client, db_session, uploads):
        from PIL import Image

        admin = _super_admin(db_session)
        user = _person(db_session, "idg-watermark@test.com")
        image = io.BytesIO()
        Image.new("RGB", (400, 300), "white").save(image, format="PNG")
        record = _legacy_upload(db_session, uploads, user.party_id, image.getvalue(), "image/png", "id.png")
        r = client.get(f"/api/identity-verifications/{record.id}/document", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content != image.getvalue()


class TestAdminCase:
    def test_the_case_shows_header_checks_and_history_without_the_number(self, client, db_session):
        admin = _super_admin(db_session)
        _user, sid, _ = _verified(client, db_session, "idg-case-view@test.com")
        r = client.get(f"/api/identity-verifications/{sid}/case", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["state"], body["roleContext"], body["countryCode"], body["providerCode"]) == (
            "VERIFIED", "OWNER", "GB", "veriff")
        assert body["providerDecision"] == "approved"
        assert body["checks"]["document_authenticity"] == "PASS"
        assert "escalated" not in body and "alternativeReason" not in body
        assert [h["eventType"] for h in body["history"]][-1] == "IDENTITY_VERIFIED"
        assert PASSPORT not in r.text
