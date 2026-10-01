"""ZR-IDENTITY-001 global identity verification: account-level profile,
country packs, the step-by-step session API, the provider adapter's
outcomes, reviewer decisions, re-verification, the signed provider webhook
and the privacy rules (masked numbers, encrypted at rest, safe events)."""

from __future__ import annotations

import io
import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import identity_verification as idv_crud
from app.models.domain_event import DomainEvent
from app.models.identity_profile import IdentityProfile, IdentityRegulatoryPack
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from app.models.verification_credential import VerificationCredential
from app.services.identity import policy, providers
from app.services.identity import service as identity_service
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

PASSPORT = "P1234567"
BASE = "/api/users/identity"


def _pdf(*lines: str) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    y = 800
    for line in lines:
        page.drawString(72, y, line)
        y -= 20
    page.save()
    return buffer.getvalue()


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "identity_upload_dir", str(tmp_path))


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


def _upload(client, user, session_id, content: bytes, *, number=PASSPORT, document_type="passport",
            filename="id.pdf", content_type="application/pdf"):
    return client.post(
        f"{BASE}/verifications/{session_id}/document",
        data={"document_type": document_type, "document_number": number},
        files={"file": (filename, content, content_type)}, cookies=auth_user_cookie(user),
    )


def _submit(client, user, session_id, attested=True):
    return client.post(f"{BASE}/verifications/{session_id}/submit", json={"attested": attested},
                       cookies=auth_user_cookie(user))


def _verified_via_pdf(client, db, email, name="Asha Rao"):
    user = _person(db, email, name)
    assert _details(client, user).status_code == 200
    sid = _start(client, user).json()["id"]
    assert _upload(client, user, sid, _pdf("PASSPORT", f"Name: {name}", f"Passport No: {PASSPORT}")).status_code == 200
    body = _submit(client, user, sid).json()
    return user, sid, body


def _super_admin(db: Session):
    existing = _super_admin_from(db)
    if existing is not None:
        return existing
    admin = _make_admin(db, email=settings.seed_admin_email, role="super_admin")
    db.commit()
    return admin


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
        countries = {c["countryCode"] for c in client.get(f"{BASE}/countries", cookies=auth_user_cookie(user)).json()}
        assert {"GB", "IN", "US"} <= countries and "*" not in countries

    def test_a_pack_edited_in_the_database_wins(self, client, db_session: Session):
        user = _person(db_session, "idg-pack-edit@test.com")
        policy.ensure_default_packs(db_session)
        gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB").one()
        gb.accepted_document_types = ["passport"]
        db_session.commit()
        assert client.get(f"{BASE}/policy?country=GB", cookies=auth_user_cookie(user)).json()["acceptedDocumentTypes"] == ["passport"]

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


class TestAutomatedOutcomes:
    def test_a_matching_document_verifies_the_account(self, client, db_session: Session, uploads):
        user, sid, body = _verified_via_pdf(client, db_session, "idg-pass@test.com")
        assert body["state"] == "VERIFIED", body
        assert body["maskedDocumentNumber"] == "••••4567"
        profile = identity_service.get_profile(db_session, user.party_id)
        assert (profile.state, profile.assurance_level, profile.verified_legal_name) == ("VERIFIED", "IV-1", "Asha Rao")
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id).id == sid
        assert idv_crud.get_valid_identity_credential(db_session, user.party_id) is not None
        record = db_session.get(IdentityVerification, sid)
        assert record.status == "verified"  # legacy readers stay in step
        assert record.match_results["document_authenticity"] == "NOT_CHECKED"  # the built-in check is honest

    def test_identity_is_not_tied_to_the_document_expiry(self, client, db_session: Session, uploads):
        user, sid, _ = _verified_via_pdf(client, db_session, "idg-noexpiry@test.com")
        profile = identity_service.get_profile(db_session, user.party_id)
        assert profile.reverification_required_at is None
        assert db_session.get(IdentityVerification, sid).expires_at is None

    def test_a_photo_with_nothing_to_read_goes_to_a_reviewer(self, client, db_session: Session, uploads):
        _super_admin(db_session)
        user = _person(db_session, "idg-photo@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        _upload(client, user, sid, b"\xff\xd8\xff fake jpeg", filename="id.jpg", content_type="image/jpeg")
        body = _submit(client, user, sid).json()
        assert body["state"] == "PENDING_REVIEW"
        assert body["reasonCodes"] == []  # internal routing reason never shown
        assert "review" in body["message"].lower()
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None

    def test_a_name_that_does_not_match_needs_action(self, client, db_session: Session, uploads):
        user = _person(db_session, "idg-mismatch@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        _upload(client, user, sid, _pdf("Name: Somebody Else", f"Passport No: {PASSPORT}"))
        body = _submit(client, user, sid).json()
        assert body["state"] == "ACTION_REQUIRED"
        assert body["reasonCodes"] == ["NAME_MISMATCH"]
        assert "does not match" in body["message"]
        assert "EDIT_DETAILS" in body["actions"]

        # Fix it: a new document on the same session, then submit again.
        assert _upload(client, user, sid, _pdf("Name: Asha Rao", f"Passport No: {PASSPORT}")).status_code == 200
        assert _submit(client, user, sid).json()["state"] == "VERIFIED"

    def test_an_invalid_number_needs_action(self, client, db_session: Session, uploads):
        user = _person(db_session, "idg-badnumber@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        _upload(client, user, sid, _pdf("Name: Asha Rao"), number="hello")
        body = _submit(client, user, sid).json()
        assert (body["state"], body["reasonCodes"]) == ("ACTION_REQUIRED", ["DOCUMENT_NUMBER_INVALID"])

    def test_the_same_document_number_on_another_account_goes_to_review(self, client, db_session: Session, uploads):
        _super_admin(db_session)
        _verified_via_pdf(client, db_session, "idg-dup-a@test.com")
        user = _person(db_session, "idg-dup-b@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        _upload(client, user, sid, _pdf("Name: Asha Rao", f"Passport No: {PASSPORT}", "copy b"))
        assert _submit(client, user, sid).json()["state"] == "PENDING_REVIEW"
        record = db_session.get(IdentityVerification, sid)
        assert record.reason_codes == ["DUPLICATE_EVIDENCE"]

    def test_under_the_minimum_age_fails(self, client, db_session: Session, uploads):
        user = _person(db_session, "idg-young@test.com")
        _details(client, user, dob=(date.today() - timedelta(days=365 * 15)).isoformat())
        sid = _start(client, user).json()["id"]
        _upload(client, user, sid, _pdf("Name: Asha Rao", f"Passport No: {PASSPORT}"))
        body = _submit(client, user, sid).json()
        assert (body["state"], body["reasonCodes"]) == ("FAILED", ["AGE_REQUIREMENT_NOT_MET"])


class TestSessionRules:
    def test_a_document_the_country_does_not_accept_is_refused(self, client, db_session: Session, uploads):
        user = _person(db_session, "idg-unsupported@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        r = _upload(client, user, sid, _pdf("x"), document_type="aadhaar")
        assert r.status_code == 400
        assert "can't be used" in r.json()["detail"]

    def test_submitting_needs_the_attestation_and_a_document(self, client, db_session: Session, uploads):
        user = _person(db_session, "idg-attest@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        assert _submit(client, user, sid).status_code == 400  # no document yet
        _upload(client, user, sid, _pdf("Name: Asha Rao", f"Passport No: {PASSPORT}"))
        assert _submit(client, user, sid, attested=False).status_code == 400

    def test_starting_twice_reuses_the_open_session_and_honours_the_idempotency_key(self, client, db_session: Session):
        user = _person(db_session, "idg-reuse@test.com")
        _details(client, user)
        first = _start(client, user, key="k-1").json()["id"]
        assert _start(client, user, key="k-1").json()["id"] == first
        assert _start(client, user).json()["id"] == first

    def test_a_verified_person_is_never_asked_again(self, client, db_session: Session, uploads):
        user, _sid, _ = _verified_via_pdf(client, db_session, "idg-again@test.com")
        assert _start(client, user).status_code == 409

    def test_a_person_cannot_read_someone_elses_session(self, client, db_session: Session):
        owner = _person(db_session, "idg-own-a@test.com")
        other = _person(db_session, "idg-own-b@test.com")
        _details(client, owner)
        sid = _start(client, owner).json()["id"]
        assert client.get(f"{BASE}/verifications/{sid}", cookies=auth_user_cookie(other)).status_code == 403


class TestAlternativeRoute:
    def test_asking_for_the_manual_route_puts_it_in_review(self, client, db_session: Session):
        _super_admin(db_session)
        user = _person(db_session, "idg-alt@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        r = client.post(f"{BASE}/verifications/{sid}/alternative", json={"reasonCode": "NO_CAMERA"},
                        cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        assert (r.json()["state"], r.json()["method"]) == ("PENDING_REVIEW", "MANUAL")
        queue = idv_crud.list_identity_verifications(db_session, _super_admin_from(db_session), status="needs_review")
        assert sid in [v.id for v in queue]

    def test_the_manual_route_is_rate_limited(self, client, db_session: Session):
        user = _person(db_session, "idg-alt-limit@test.com")
        _details(client, user)
        for _ in range(3):
            session = IdentityVerification(party_id=user.party_id, session_state="FAILED", status="rejected",
                                           alternative_reason="OTHER", updated_at=datetime.now(timezone.utc))
            db_session.add(session)
        db_session.commit()
        sid = _start(client, user).json()["id"]
        r = client.post(f"{BASE}/verifications/{sid}/alternative", json={"reasonCode": "OTHER"},
                        cookies=auth_user_cookie(user))
        assert r.status_code == 429


def _super_admin_from(db: Session):
    from app.models.admin_user import AdminUser

    return db.query(AdminUser).filter(AdminUser.role == "super_admin").first()


class TestReviewerDecisions:
    def _in_review(self, client, db, email):
        admin = _super_admin(db)
        user = _person(db, email)
        _details(client, user)
        sid = _start(client, user).json()["id"]
        _upload(client, user, sid, b"\xff\xd8\xff jpeg", filename="id.jpg", content_type="image/jpeg")
        _submit(client, user, sid)
        return admin, user, sid

    def _decide(self, client, admin, sid, **body):
        return client.post(f"/api/identity-verifications/{sid}/decision", json=body, cookies=auth_admin_cookie(admin))

    def test_approval_needs_a_reason_and_a_note_and_verifies_at_the_reviewed_level(self, client, db_session, uploads):
        admin, user, sid = self._in_review(client, db_session, "idg-rev-ok@test.com")
        assert self._decide(client, admin, sid, decision="APPROVE", reasonCode="REVIEWER_APPROVED", note=" ").status_code == 400
        r = self._decide(client, admin, sid, decision="APPROVE", reasonCode="REVIEWER_APPROVED", note="Photo checked")
        assert r.status_code == 200, r.text
        assert r.json()["sessionState"] == "VERIFIED"
        profile = identity_service.get_profile(db_session, user.party_id)
        assert (profile.state, profile.assurance_level) == ("VERIFIED", "IV-2")
        event = db_session.query(DomainEvent).filter(DomainEvent.event_type == "IDENTITY_REVIEWER_DECISION").one()
        assert (event.previous_state, event.new_state) == ("PENDING_REVIEW", "VERIFIED")

    def test_action_required_and_reject(self, client, db_session, uploads):
        admin, user, sid = self._in_review(client, db_session, "idg-rev-ar@test.com")
        r = self._decide(client, admin, sid, decision="ACTION_REQUIRED", reasonCode="DOCUMENT_UNREADABLE",
                         note="Too dark")
        assert r.json()["sessionState"] == "ACTION_REQUIRED"
        r = self._decide(client, admin, sid, decision="REJECT", reasonCode="REVIEWER_REJECTED", note="Not genuine")
        assert r.json()["sessionState"] == "FAILED"
        assert identity_service.get_profile(db_session, user.party_id).state == "FAILED"

    def test_escalation_keeps_it_in_review_and_moves_it_to_the_front(self, client, db_session, uploads):
        admin, _user, first = self._in_review(client, db_session, "idg-rev-esc1@test.com")
        _a, _u, second = self._in_review(client, db_session, "idg-rev-esc2@test.com")
        r = self._decide(client, admin, second, decision="ESCALATE", reasonCode="ESCALATED", note="Needs a senior look")
        assert r.json()["sessionState"] == "PENDING_REVIEW"
        queue = idv_crud.list_identity_verifications(db_session, admin, status="needs_review")
        assert [v.id for v in queue][:2] == [second, first]

    def test_an_unknown_reason_code_is_refused(self, client, db_session, uploads):
        admin, _user, sid = self._in_review(client, db_session, "idg-rev-bad@test.com")
        assert self._decide(client, admin, sid, decision="APPROVE", reasonCode="BECAUSE", note="x").status_code == 400


class TestReverification:
    def test_changing_the_verified_legal_name_needs_the_password(self, client, db_session, uploads):
        """Section 9.3 step-up authentication."""
        user, _sid, _ = _verified_via_pdf(client, db_session, "idg-stepup@test.com")
        assert _details(client, user, given="Asha", family="Kumar").status_code == 403
        assert _details(client, user, given="Asha", family="Kumar", password="wrong").status_code == 403
        profile = identity_service.get_profile(db_session, user.party_id)
        db_session.refresh(profile)
        assert (profile.state, profile.family_name) == ("VERIFIED", "Rao")

    def test_changing_the_verified_legal_name_requires_verifying_again(self, client, db_session, uploads):
        user, _sid, _ = _verified_via_pdf(client, db_session, "idg-rename@test.com")
        r = _details(client, user, given="Asha", family="Kumar", password="password123")
        assert r.json()["state"] == "REVERIFICATION_REQUIRED"
        assert r.json()["dashboard"]["primaryAction"] == "Verify again"
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None
        assert idv_crud.get_valid_identity_credential(db_session, user.party_id) is None
        # And they can start a fresh session.
        assert _start(client, user).status_code == 201

    def test_a_cosmetic_change_does_not(self, client, db_session, uploads):
        user, _sid, _ = _verified_via_pdf(client, db_session, "idg-case@test.com")
        assert _details(client, user, given="ASHA", family="rao").json()["state"] == "VERIFIED"

    def test_periodic_renewal_from_the_country_pack(self, client, db_session, uploads):
        policy.ensure_default_packs(db_session)
        gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB").one()
        gb.reverification_interval_days = 30
        db_session.commit()
        user, _sid, _ = _verified_via_pdf(client, db_session, "idg-renew@test.com")
        profile = identity_service.get_profile(db_session, user.party_id)
        assert profile.reverification_required_at is not None
        profile.reverification_required_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        assert identity_service.sweep_reverification_due(db_session) == 1
        db_session.refresh(profile)
        assert (profile.state, profile.reason_codes) == ("REVERIFICATION_REQUIRED", ["PERIODIC_RENEWAL"])

    def test_account_recovery_only_where_the_pack_requires_it(self, client, db_session, uploads):
        user, _sid, _ = _verified_via_pdf(client, db_session, "idg-recover@test.com")
        identity_service.on_account_recovery(db_session, user)
        assert identity_service.get_profile(db_session, user.party_id).state == "VERIFIED"
        gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB").one()
        gb.reverify_on_account_recovery = True
        db_session.commit()
        identity_service.on_account_recovery(db_session, user)
        assert identity_service.get_profile(db_session, user.party_id).state == "REVERIFICATION_REQUIRED"


class TestProviderWebhook:
    SECRET = "test-identity-secret"

    @pytest.fixture()
    def webhook_provider(self, monkeypatch):
        monkeypatch.setattr(settings, "identity_webhook_secret", self.SECRET)

    def _processing_session(self, db, email):
        user = _person(db, email)
        profile = identity_service.get_or_create_profile(db, user.party_id)
        profile.country_code, profile.date_of_birth = "GB", date(1990, 1, 1)
        session = IdentityVerification(
            party_id=user.party_id, session_state="PROCESSING", status="pending", method_type="DOCUMENT",
            provider_code="signed_webhook", provider_session_id="vendor-123", legal_name_snapshot="Asha Rao",
        )
        db.add(session)
        db.commit()
        return user, session

    def _post(self, client, body: dict, *, secret=None, timestamp=None):
        raw = json.dumps(body).encode("utf-8")
        ts = str(timestamp if timestamp is not None else int(time.time()))
        signature = providers.sign(secret or self.SECRET, ts, raw)
        return client.post("/api/webhooks/identity/signed_webhook", content=raw, headers={
            "X-Identity-Timestamp": ts, "X-Identity-Signature": f"sha256={signature}",
            "Content-Type": "application/json",
        })

    def _event(self, event_id="evt-1", outcome="PASS"):
        return {"events": [{"id": event_id, "type": "verification.completed", "provider_session_id": "vendor-123",
                            "outcome": outcome, "match_results": {"document_authenticity": "PASS"}}]}

    def test_a_signed_result_is_applied_once(self, client, db_session, webhook_provider):
        user, session = self._processing_session(db_session, "idg-hook@test.com")
        r = self._post(client, self._event())
        assert r.status_code == 200, r.text
        assert r.json() == {"accepted": 1, "duplicates": 0, "processed": 1}
        db_session.expire_all()
        assert db_session.get(IdentityVerification, session.id).session_state == "VERIFIED"
        # Replay of the same event id: recognised, not reprocessed.
        assert self._post(client, self._event()).json() == {"accepted": 0, "duplicates": 1, "processed": 0}

    def test_a_bad_signature_or_an_old_timestamp_is_refused(self, client, db_session, webhook_provider):
        self._processing_session(db_session, "idg-hook-bad@test.com")
        assert self._post(client, self._event(), secret="wrong").status_code == 401
        assert self._post(client, self._event(), timestamp=int(time.time()) - 3600).status_code == 401

    def test_unknown_provider_is_404(self, client):
        assert client.post("/api/webhooks/identity/nobody", content=b"{}").status_code == 404

    def test_the_provider_invalidating_a_pass_requires_reverification(self, client, db_session, webhook_provider):
        user, session = self._processing_session(db_session, "idg-hook-inv@test.com")
        self._post(client, self._event("evt-a"))
        self._post(client, self._event("evt-b", outcome="FAIL"))
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
    def test_the_number_is_encrypted_at_rest_and_only_ever_returned_masked(self, client, db_session, uploads):
        user, sid, body = _verified_via_pdf(client, db_session, "idg-privacy@test.com")
        record = db_session.get(IdentityVerification, sid)
        assert record.encrypted_reference and PASSPORT not in record.encrypted_reference
        assert PASSPORT not in json.dumps(body)
        admin = _super_admin(db_session)
        listed = client.get("/api/identity-verifications", cookies=auth_admin_cookie(admin)).text
        assert PASSPORT not in listed and "encryptedReference" not in listed
        mine = client.get("/api/users/identity-verifications", cookies=auth_user_cookie(user)).text
        assert PASSPORT not in mine

    def test_events_carry_no_names_numbers_or_dates_of_birth(self, client, db_session, uploads):
        _verified_via_pdf(client, db_session, "idg-events@test.com")
        events = db_session.query(DomainEvent).filter(DomainEvent.event_type.like("IDENTITY_%")).all()
        types = {e.event_type for e in events}
        assert {"IDENTITY_VERIFICATION_STARTED", "IDENTITY_METHOD_SELECTED", "IDENTITY_EVIDENCE_CAPTURED",
                "IDENTITY_PROVIDER_RESULT_RECEIVED", "IDENTITY_VERIFIED"} <= types
        dump = json.dumps([e.payload for e in events])
        for secret in (PASSPORT, "Asha", "1990-05-01"):
            assert secret not in dump


class TestRetention:
    def test_evidence_past_the_retention_period_is_deleted_but_the_decision_is_kept(
        self, client, db_session, uploads, tmp_path,
    ):
        user, sid, _ = _verified_via_pdf(client, db_session, "idg-retention@test.com")
        record = db_session.get(IdentityVerification, sid)
        stored = tmp_path / record.document_file_path
        assert stored.is_file()
        assert identity_service.purge_expired_evidence(db_session) == 0  # still inside the period

        record.decided_at = datetime.now(timezone.utc) - timedelta(days=400)
        db_session.commit()
        assert identity_service.purge_expired_evidence(db_session) == 1
        db_session.refresh(record)
        assert record.document_file_path is None and record.evidence_purged_at is not None
        assert not stored.exists()
        assert record.masked_document_number == "••••4567"
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id).id == sid

    def test_nothing_is_purged_under_a_pack_without_a_retention_period(self, client, db_session, uploads):
        _user, sid, _ = _verified_via_pdf(client, db_session, "idg-retention-none@test.com")
        gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB").one()
        gb.evidence_retention_days = None
        record = db_session.get(IdentityVerification, sid)
        record.decided_at = datetime.now(timezone.utc) - timedelta(days=4000)
        db_session.commit()
        assert identity_service.purge_expired_evidence(db_session) == 0


class TestMetrics:
    def test_metrics_are_aggregates_only(self, client, db_session, uploads):
        admin = _super_admin(db_session)
        _verified_via_pdf(client, db_session, "idg-metrics@test.com")
        r = client.get("/api/identity-verifications/metrics", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["verified"] >= 1 and body["startToVerifiedRate"] is not None
        assert "DOCUMENT" in body["byMethod"]
        assert PASSPORT not in r.text and "Asha" not in r.text


class TestSecureEvidenceViewer:
    def test_the_document_is_shown_inline_never_cached_and_the_view_is_logged(self, client, db_session, uploads):
        from app.models.audit import AuditEvent

        admin = _super_admin(db_session)
        _user, sid, _ = _verified_via_pdf(client, db_session, "idg-viewer@test.com")
        r = client.get(f"/api/identity-verifications/{sid}/document", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200
        assert r.headers["content-disposition"] == "inline"
        assert "no-store" in r.headers["cache-control"]
        assert db_session.query(AuditEvent).filter(AuditEvent.action == "identity_verification.evidence_viewed").count() == 1

    def test_images_are_watermarked(self, client, db_session, uploads):
        from PIL import Image

        admin = _super_admin(db_session)
        user = _person(db_session, "idg-watermark@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        image = io.BytesIO()
        Image.new("RGB", (400, 300), "white").save(image, format="PNG")
        _upload(client, user, sid, image.getvalue(), filename="id.png", content_type="image/png")
        r = client.get(f"/api/identity-verifications/{sid}/document", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content != image.getvalue()


class TestReviewerCase:
    def test_the_case_shows_header_checks_and_history_without_the_number(self, client, db_session, uploads):
        admin = _super_admin(db_session)
        user = _person(db_session, "idg-case@test.com")
        _details(client, user)
        sid = _start(client, user).json()["id"]
        _upload(client, user, sid, b"\xff\xd8\xff jpeg", filename="id.jpg", content_type="image/jpeg")
        _submit(client, user, sid)
        r = client.get(f"/api/identity-verifications/{sid}/case", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["state"], body["roleContext"], body["countryCode"]) == ("PENDING_REVIEW", "OWNER", "GB")
        assert body["reasonCodes"] == ["NAME_NOT_CHECKABLE"]
        assert body["checks"]["document_authenticity"] == "NOT_CHECKED"
        assert [h["eventType"] for h in body["history"]][-1] == "IDENTITY_MANUAL_REVIEW_STARTED"
        assert PASSPORT not in r.text
