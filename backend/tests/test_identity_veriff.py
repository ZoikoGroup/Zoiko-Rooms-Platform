"""ZR-IDV-ADR-001 -- Veriff Document + Selfie IDV behind the Zoiko provider
abstraction. Covers Section 16 acceptance criterion 15: approved, review,
resubmission_requested, declined, expired, abandoned, duplicate webhook,
invalid HMAC, stale session and provider outage -- plus out-of-order
delivery, the event webhook never verifying, and credentials never
reaching the client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import identity_verification as idv_crud
from app.models.identity_profile import IdentityProviderEvent, IdentityRegulatoryPack
from app.models.identity_verification import IdentityVerification
from app.models.party import Party
from app.services.identity import policy
from app.services.identity import service as identity_service
from tests.conftest import stored_refs, _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

API_KEY = "veriff-test-key"
SECRET = "veriff-test-secret"
BASE = "/api/users/identity"
VERIFF_URL = "https://alchemy.veriff.com/v/session-token-xyz"


class FakeVeriff:
    """Stands in for Veriff's API: records requests, returns canned replies."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.down = False
        self.decision: dict | None = None
        self._counter = 0

    def __call__(self, method, url, content=None, headers=None, timeout=None):
        self.calls.append((method, url, dict(headers or {}), content))
        request = httpx.Request(method, url)
        if self.down:
            raise httpx.ConnectError("down", request=request)
        if method == "POST" and url.endswith("/v1/sessions"):
            self._counter += 1
            return httpx.Response(201, json={"status": "success", "verification": {
                "id": f"veriff-session-{self._counter}", "url": f"{VERIFF_URL}-{self._counter}",
                "sessionToken": "legacy-token-not-used", "vendorData": json.loads(content)["verification"]["vendorData"],
            }}, request=request)
        if method == "POST" and url.endswith("/media"):
            return httpx.Response(200, json={"status": "success", "image": {"id": "img"}}, request=request)
        if method == "PATCH" and "/v1/sessions/" in url:
            return httpx.Response(200, json={"status": "success"}, request=request)
        if method == "GET" and url.endswith("/decision"):
            return httpx.Response(200, json={"status": "success", "verification": self.decision}, request=request)
        if method == "DELETE":
            return httpx.Response(202, json={"status": "success"}, request=request)
        return httpx.Response(404, request=request)


@pytest.fixture()
def veriff(monkeypatch):
    monkeypatch.setattr(settings, "veriff_api_key", API_KEY)
    monkeypatch.setattr(settings, "veriff_shared_secret", SECRET)
    fake = FakeVeriff()
    monkeypatch.setattr(httpx, "request", fake)
    return fake


@pytest.fixture()
def veriff_pack(db_session: Session, veriff):
    policy.ensure_default_packs(db_session)
    gb = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB", active=True).one()
    gb.document_provider_code = "veriff"
    db_session.commit()
    return gb


def _person(db: Session, email: str):
    party = Party(party_type="renter", status="active", jurisdiction="England")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.full_name = "Asha Rao"
    user.party_id = party.id
    db.commit()
    return user


def _launch(client, user) -> dict:
    """Details -> session -> attest & submit: returns the submit response."""
    r = client.put(f"{BASE}/details", json={"givenName": "Asha", "familyName": "Rao", "dateOfBirth": "1990-05-01",
                                            "countryCode": "GB"}, cookies=auth_user_cookie(user))
    assert r.status_code == 200, r.text
    sid = client.post(f"{BASE}/verifications", json={"method": "DOCUMENT", "roleContext": "AGENT"},
                      cookies=auth_user_cookie(user)).json()["id"]
    r = client.post(f"{BASE}/verifications/{sid}/submit", json={"attested": True}, cookies=auth_user_cookie(user))
    assert r.status_code == 200, r.text
    return r.json()


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _decision_body(session: IdentityVerification, status: str, *, attempt="att-1", reason_code=None, **extra) -> bytes:
    verification = {
        "id": session.provider_session_id, "attemptId": attempt, "status": status, "code": 9001,
        "vendorData": f"zr-idv-{session.id}", "reasonCode": reason_code,
        "person": {"firstName": "Asha", "lastName": "Rao", "dateOfBirth": "1990-05-01"},
        "document": {"number": "P1234567", "type": "PASSPORT", "country": "GB", "validUntil": "2031-01-01"},
        **extra,
    }
    return json.dumps({"status": "success", "verification": verification}).encode()


def _post_decision(client, body: bytes, *, key=API_KEY, signature=None):
    return client.post("/api/v1/webhooks/veriff/decision", content=body, headers={
        "x-auth-client": key, "x-hmac-signature": signature or _sign(body), "Content-Type": "application/json",
    })


def _jpeg(width: int = 320, height: int = 240) -> bytes:
    """A real JPEG with enough detail to pass the minimum-size check."""
    import io
    import random

    from PIL import Image

    image = Image.new("RGB", (width, height))
    image.putdata([(random.randrange(256), random.randrange(256), random.randrange(256)) for _ in range(width * height)])
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _capture(client, user, session_id: int, context: str, *, document_type: str = "", content: bytes | None = None,
             filename: str = "photo.jpg"):
    return client.post(f"{BASE}/verifications/{session_id}/capture",
                       data={"context": context, "document_type": document_type},
                       files={"file": (filename, content if content is not None else _jpeg(), "image/jpeg")},
                       cookies=auth_user_cookie(user))


def _complete(client, user, session_id: int):
    return client.post(f"{BASE}/verifications/{session_id}/complete", cookies=auth_user_cookie(user))


def _session(db: Session, session_id: int) -> IdentityVerification:
    db.expire_all()
    return db.get(IdentityVerification, session_id)


class TestSessionCreation:
    def test_an_http_site_sends_no_return_url(self, client, db_session, veriff_pack, veriff, monkeypatch):
        """Veriff rejects non-HTTPS return URLs (error 1302), e.g. localhost."""
        monkeypatch.setattr(settings, "frontend_url", "http://localhost:3000")
        user = _person(db_session, "vf-http@test.com")
        assert _launch(client, user)["captureAvailable"] is True
        sent = json.loads(veriff.calls[-1][3])["verification"]
        assert "callback" not in sent

    def test_the_backend_creates_the_session_and_the_client_never_gets_a_veriff_url(self, client, db_session, veriff_pack,
                                                                                     veriff, monkeypatch):
        monkeypatch.setattr(settings, "frontend_url", "https://zoikorooms.com")
        user = _person(db_session, "vf-create@test.com")
        policy_body = client.get(f"{BASE}/policy?country=GB", cookies=auth_user_cookie(user)).json()
        assert (policy_body["captureMode"], policy_body["selfieCheck"]) == ("PROVIDER_HOSTED", True)

        body = _launch(client, user)
        assert (body["state"], body["captureAvailable"], body["captured"]) == ("IN_PROGRESS", True, [])
        dump = json.dumps(body)
        assert API_KEY not in dump and SECRET not in dump and "legacy-token" not in dump
        assert VERIFF_URL not in dump and "launchUrl" not in body  # capture happens on Zoiko's own screens

        method, url, headers, content = veriff.calls[-1]
        assert method == "POST" and url == "https://stationapi.veriff.com/v1/sessions"
        assert headers["X-AUTH-CLIENT"] == API_KEY
        sent = json.loads(content)["verification"]
        assert sent["vendorData"] == f"zr-idv-{body['id']}"  # opaque Zoiko reference only
        assert "Asha" not in json.dumps(sent) and user.email not in json.dumps(sent)
        assert sent["callback"] == "https://zoikorooms.com/account/identity?verification=returned"

        record = _session(db_session, body["id"])
        assert record.provider_session_id == "veriff-session-1"
        assert record.provider_session_url_encrypted and VERIFF_URL not in record.provider_session_url_encrypted
        assert record.consent_notice_version.startswith("GB:v")
        assert record.document_file_path is None  # no raw media copied into Zoiko

    def test_there_is_no_route_that_sends_the_person_to_veriff(self, client, db_session, veriff_pack):
        user = _person(db_session, "vf-relaunch@test.com")
        sid = _launch(client, user)["id"]
        assert client.post(f"{BASE}/verifications/{sid}/launch", cookies=auth_user_cookie(user)).status_code in (404, 405)

    def test_provider_outage_keeps_progress_and_never_verifies(self, client, db_session, veriff_pack, veriff):
        veriff.down = True
        user = _person(db_session, "vf-outage@test.com")
        body = _launch(client, user)
        assert (body["state"], body["reasonCodes"]) == ("IN_PROGRESS", ["PROVIDER_UNAVAILABLE"])
        assert body["captureAvailable"] is False
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None
        veriff.down = False  # and it works on retry
        r = client.post(f"{BASE}/verifications/{body['id']}/submit", json={"attested": True}, cookies=auth_user_cookie(user))
        assert r.json()["captureAvailable"] is True

    def test_without_credentials_the_veriff_pack_is_unavailable(self, client, db_session, veriff_pack, monkeypatch):
        monkeypatch.setattr(settings, "veriff_api_key", "")
        user = _person(db_session, "vf-nocreds@test.com")
        assert client.get(f"{BASE}/policy?country=GB", cookies=auth_user_cookie(user)).json()["providerAvailable"] is False
        body = _launch(client, user)
        assert body["reasonCodes"] == ["PROVIDER_UNAVAILABLE"]


class TestDecisionMapping:
    @pytest.fixture()
    def launched(self, client, db_session, veriff_pack):
        user = _person(db_session, f"vf-map-{id(self)}@test.com")
        sid = _launch(client, user)["id"]
        return user, _session(db_session, sid)

    def test_approved_verifies_with_the_providers_name(self, client, db_session, launched):
        _admin = _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user, session = launched
        r = _post_decision(client, _decision_body(session, "approved"))
        assert r.status_code == 200, r.text
        assert r.json() == {"accepted": 1, "duplicates": 0}
        record = _session(db_session, session.id)
        assert (record.session_state, record.provider_decision, record.assurance_level) == ("VERIFIED", "approved", "IV-1")
        assert record.masked_document_number == "••••4567"
        assert record.match_results["person_document_binding"] == "PASS"
        profile = identity_service.get_profile(db_session, user.party_id)
        assert profile.verified_legal_name == "Asha Rao"
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id).id == session.id

    def test_review_stays_checking_and_is_not_put_in_the_zoiko_queue(self, client, db_session, launched):
        user, session = launched
        _post_decision(client, _decision_body(session, "review"))
        record = _session(db_session, session.id)
        assert record.session_state == "PROCESSING"
        assert identity_service.get_profile(db_session, user.party_id).state == "PROCESSING"
        admin = _make_admin(db_session, email="vf-review-admin@test.com", role="super_admin")
        queue = idv_crud.list_identity_verifications(db_session, admin, status="needs_review")
        assert session.id not in [v.id for v in queue]
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None
        # ...and the provider's final answer still lands.
        _post_decision(client, _decision_body(session, "approved", attempt="att-1b"))
        assert _session(db_session, session.id).session_state == "VERIFIED"

    def test_resubmission_requested_needs_action_and_new_photos_can_be_taken(self, client, db_session, launched):
        user, session = launched
        _post_decision(client, _decision_body(session, "resubmission_requested", reason_code=204))
        record = _session(db_session, session.id)
        assert (record.session_state, record.reason_codes) == ("ACTION_REQUIRED", ["DOCUMENT_UNREADABLE"])
        r = client.get(f"{BASE}/verifications/{session.id}", cookies=auth_user_cookie(user)).json()
        assert r["captureAvailable"] is True
        body = _capture(client, user, session.id, "document-front", document_type="passport").json()
        assert (body["state"], body["captured"]) == ("IN_PROGRESS", ["document-front"])
        _capture(client, user, session.id, "face")
        assert _complete(client, user, session.id).json()["state"] == "PROCESSING"
        # The new attempt is approved.
        _post_decision(client, _decision_body(session, "approved", attempt="att-2"))
        assert _session(db_session, session.id).session_state == "VERIFIED"

    def test_declined_fails_with_safe_copy(self, client, db_session, launched):
        user, session = launched
        _post_decision(client, _decision_body(session, "declined", reason_code=102, reason="Suspected document tampering"))
        record = _session(db_session, session.id)
        assert (record.session_state, record.reason_codes) == ("FAILED", ["PROVIDER_DECLINED"])
        r = client.get(f"{BASE}/verifications/{session.id}", cookies=auth_user_cookie(user)).json()
        assert "tampering" not in json.dumps(r).lower() and "fraud" not in json.dumps(r).lower()
        assert r["canRestart"] is True

    @pytest.mark.parametrize("decision, code", [("expired", "SESSION_EXPIRED"), ("abandoned", "SESSION_ABANDONED")])
    def test_expired_and_abandoned_need_a_restart(self, client, db_session, launched, decision, code):
        user, session = launched
        _post_decision(client, _decision_body(session, decision))
        record = _session(db_session, session.id)
        assert (record.session_state, record.reason_codes) == ("ACTION_REQUIRED", [code])
        r = client.get(f"{BASE}/verifications/{session.id}", cookies=auth_user_cookie(user)).json()
        assert (r["canRestart"], r["captureAvailable"]) == (True, False)

    def test_an_unknown_decision_never_verifies(self, client, db_session, launched):
        _user, session = launched
        _post_decision(client, _decision_body(session, "something_new"))
        assert _session(db_session, session.id).session_state == "FAILED"


class TestWebhookContract:
    @pytest.fixture()
    def launched(self, client, db_session, veriff_pack):
        user = _person(db_session, f"vf-hook-{id(self)}@test.com")
        sid = _launch(client, user)["id"]
        return user, _session(db_session, sid)

    def test_invalid_hmac_or_client_is_rejected_before_parsing(self, client, db_session, launched):
        _user, session = launched
        body = _decision_body(session, "approved")
        assert _post_decision(client, body, signature="0" * 64).status_code == 401
        assert _post_decision(client, body, key="someone-else").status_code == 401
        assert _post_decision(client, body, signature=_sign(body + b" ")).status_code == 401  # raw bytes, not re-serialized
        assert _session(db_session, session.id).session_state == "IN_PROGRESS"
        assert db_session.query(IdentityProviderEvent).filter_by(status="rejected").count() == 3

    def test_a_duplicate_delivery_is_acknowledged_and_not_reprocessed(self, client, db_session, launched):
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        _user, session = launched
        body = _decision_body(session, "approved")
        assert _post_decision(client, body).json()["accepted"] == 1
        r = _post_decision(client, body)
        assert (r.status_code, r.json()) == (200, {"accepted": 0, "duplicates": 1})
        row = db_session.query(IdentityProviderEvent).filter_by(status="processed").one()
        assert row.duplicate_count == 1 and row.payload_encrypted is None  # content dropped after processing

    def test_out_of_order_delivery_never_regresses_verified(self, client, db_session, launched):
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        _user, session = launched
        _post_decision(client, _decision_body(session, "approved"))
        late = json.dumps({"id": session.provider_session_id, "attemptId": "att-1", "action": "started", "code": 7001,
                           "vendorData": f"zr-idv-{session.id}"}).encode()
        r = client.post("/api/v1/webhooks/veriff/events", content=late,
                        headers={"x-auth-client": API_KEY, "x-hmac-signature": _sign(late)})
        assert r.status_code == 200
        _post_decision(client, _decision_body(session, "expired", attempt="att-0"))
        assert _session(db_session, session.id).session_state == "VERIFIED"

    def test_the_event_webhook_never_verifies(self, client, db_session, launched):
        user, session = launched
        body = json.dumps({"id": session.provider_session_id, "attemptId": "att-1", "action": "submitted",
                           "code": 7002, "vendorData": f"zr-idv-{session.id}", "status": "approved"}).encode()
        r = client.post("/api/v1/webhooks/veriff/events", content=body,
                        headers={"x-auth-client": API_KEY, "x-hmac-signature": _sign(body)})
        assert r.status_code == 200
        assert _session(db_session, session.id).session_state == "PROCESSING"  # "submitted" -> checking
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None

    def test_a_mismatched_vendor_reference_is_ignored(self, client, db_session, launched):
        _user, session = launched
        body = json.loads(_decision_body(session, "approved"))
        body["verification"]["vendorData"] = "zr-idv-999999"
        raw = json.dumps(body).encode()
        _post_decision(client, raw)
        assert _session(db_session, session.id).session_state == "IN_PROGRESS"

    def test_an_accepted_but_unprocessed_event_is_picked_up_by_the_sweeper(self, client, db_session, launched):
        """Durable acceptance: an event recorded but not processed (e.g. the
        process died after acknowledging) is applied by the scheduled job."""
        from app.core.field_encryption import encrypt_text
        from app.services.identity.providers import VeriffProvider

        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        _user, session = launched
        [event] = VeriffProvider(API_KEY, SECRET, "https://x", 1).parse_webhook(_decision_body(session, "approved"))
        db_session.add(IdentityProviderEvent(
            provider_code="veriff", provider_event_id=event.provider_event_id, identity_verification_id=session.id,
            event_type="decision", status="accepted",
            payload_encrypted=encrypt_text(json.dumps({"event_type": "decision", "provider_session_id": session.provider_session_id,
                                                       "result": event.result.to_json()})),
        ))
        db_session.commit()
        assert identity_service.process_pending_webhook_events(db_session) == 1
        assert _session(db_session, session.id).session_state == "VERIFIED"
        # A redelivery of the same decision is now a duplicate.
        assert _post_decision(client, _decision_body(session, "approved")).json()["duplicates"] == 1


class TestReconciliationAndRestart:
    def test_a_stale_session_is_reconciled_from_the_decision_api(self, client, db_session, veriff_pack, veriff):
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user = _person(db_session, "vf-stale@test.com")
        sid = _launch(client, user)["id"]
        record = _session(db_session, sid)
        record.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
        db_session.commit()
        veriff.decision = None
        assert identity_service.reconcile_stale_sessions(db_session) == 0  # nothing yet
        record = _session(db_session, sid)
        record.updated_at = datetime.now(timezone.utc) - timedelta(hours=3)
        db_session.commit()
        veriff.decision = json.loads(_decision_body(record, "approved"))["verification"]
        assert identity_service.reconcile_stale_sessions(db_session) == 1
        assert _session(db_session, sid).session_state == "VERIFIED"
        method, url, headers, _ = veriff.calls[-1]
        assert method == "GET" and headers["X-HMAC-SIGNATURE"] == _sign(record.provider_session_id.encode())

    def test_restart_after_expiry_starts_a_new_attempt_within_the_daily_limit(self, client, db_session, veriff_pack):
        user = _person(db_session, "vf-restart@test.com")
        sid = _launch(client, user)["id"]
        _post_decision(client, _decision_body(_session(db_session, sid), "expired"))
        r = client.post(f"{BASE}/verifications/{sid}/restart", cookies=auth_user_cookie(user))
        assert r.status_code == 201, r.text
        new_id = r.json()["id"]
        assert new_id != sid and _session(db_session, sid).session_state == "FAILED"
        submitted = client.post(f"{BASE}/verifications/{new_id}/submit", json={"attested": True}, cookies=auth_user_cookie(user))
        assert submitted.json()["captureAvailable"] is True
        assert _session(db_session, new_id).provider_session_id == "veriff-session-2"

        veriff_pack_now = db_session.query(IdentityRegulatoryPack).filter_by(country_code="GB", active=True).one()
        veriff_pack_now.max_attempts_per_day = 2
        db_session.commit()
        _post_decision(client, _decision_body(_session(db_session, new_id), "abandoned"))
        assert client.post(f"{BASE}/verifications/{new_id}/restart", cookies=auth_user_cookie(user)).status_code == 429

    def test_an_open_capture_cannot_be_restarted(self, client, db_session, veriff_pack):
        user = _person(db_session, "vf-norestart@test.com")
        sid = _launch(client, user)["id"]
        assert client.post(f"{BASE}/verifications/{sid}/restart", cookies=auth_user_cookie(user)).status_code == 409


class TestTrustBoundaryAndPrivacy:
    def test_identity_never_sets_property_or_authority_verification(self, client, db_session, veriff_pack):
        from app.models.authority_record import AuthorityRecord
        from app.models.property_verification import PropertyVerification

        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user = _person(db_session, "vf-boundary@test.com")
        sid = _launch(client, user)["id"]
        _post_decision(client, _decision_body(_session(db_session, sid), "approved"))
        assert db_session.query(AuthorityRecord).filter_by(party_id=user.party_id).count() == 0
        assert db_session.query(PropertyVerification).count() == 0

    def test_erasure_clears_zoiko_data_and_asks_veriff_to_delete(self, client, db_session, veriff_pack, veriff):
        admin = _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user = _person(db_session, "vf-erase@test.com")
        sid = _launch(client, user)["id"]
        _post_decision(client, _decision_body(_session(db_session, sid), "approved"))
        r = client.post(f"/api/identity-verifications/parties/{user.party_id}/erase", json={"reason": "DSAR #42"},
                        cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["providerDeleted"] == 1
        assert veriff.calls[-1][0] == "DELETE"
        profile = identity_service.get_profile(db_session, user.party_id)
        db_session.refresh(profile)
        assert (profile.state, profile.given_name, profile.verified_legal_name) == ("NOT_STARTED", "", "")
        record = _session(db_session, sid)
        assert record.provider_session_url_encrypted is None and record.legal_name_snapshot == ""
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None

    def test_editing_a_pack_creates_a_new_version(self, client, db_session, veriff_pack):
        admin = _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        r = client.put(f"/api/identity-verifications/packs/{veriff_pack.id}",
                       json={"privacyNoticeText": "Updated notice", "maxAttemptsPerDay": 3}, cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        assert r.json()["version"] == veriff_pack.version + 1
        db_session.refresh(veriff_pack)
        assert veriff_pack.active is False
        bad = client.put(f"/api/identity-verifications/packs/{r.json()['id']}", json={"documentProviderCode": "nope"},
                         cookies=auth_admin_cookie(admin))
        assert bad.status_code == 400

    def test_metrics_include_provider_and_webhook_health(self, client, db_session, veriff_pack):
        admin = _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user = _person(db_session, "vf-metrics@test.com")
        sid = _launch(client, user)["id"]
        body = _decision_body(_session(db_session, sid), "approved")
        _post_decision(client, body, signature="bad")
        _post_decision(client, body)
        m = client.get("/api/identity-verifications/metrics", cookies=auth_admin_cookie(admin)).json()
        assert m["providerOutcomes"] == {"approved": 1}
        assert (m["webhookAuthFailures"], m["webhookEvents"]) == (1, 1)
        assert "Asha" not in json.dumps(m)


class TestContractDependentBehaviour:
    """ADR Section 2: the webhook contract and reason codes depend on the
    contracted Veriff plan -- configurable, not hard-coded."""

    def test_full_auto_webhook_is_supported_for_the_essential_plan(self, client, db_session, veriff_pack, monkeypatch):
        monkeypatch.setattr(settings, "veriff_plan", "full_auto")
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user = _person(db_session, "vf-fullauto@test.com")
        sid = _launch(client, user)["id"]
        session = _session(db_session, sid)
        body = json.dumps({
            "eventType": "fullauto", "sessionId": session.provider_session_id, "attemptId": "att-1",
            "vendorData": f"zr-idv-{session.id}",
            "data": {"verification": {"decision": {"value": "approved"},
                                      "person": {"firstName": {"value": "Asha"}, "lastName": {"value": "Rao"}},
                                      "document": {"number": {"value": "P7654321"}}}},
        }).encode()
        r = client.post("/api/v1/webhooks/veriff/full-auto", content=body,
                        headers={"x-auth-client": API_KEY, "x-hmac-signature": _sign(body)})
        assert r.status_code == 200 and r.json()["accepted"] == 1
        record = _session(db_session, sid)
        assert (record.session_state, record.masked_document_number) == ("VERIFIED", "••••4321")

    def test_reason_codes_come_from_the_database_mapping(self, client, db_session, veriff_pack):
        admin = _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        r = client.put("/api/identity-verifications/providers/reason-mappings", json={
            "providerDecision": "resubmission_requested", "providerReasonCode": "999",
            "zoikoReasonCode": "NAME_MISMATCH", "description": "contract-specific",
        }, cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        user = _person(db_session, "vf-mapping@test.com")
        sid = _launch(client, user)["id"]
        _post_decision(client, _decision_body(_session(db_session, sid), "resubmission_requested", reason_code=999))
        assert _session(db_session, sid).reason_codes == ["NAME_MISMATCH"]
        # An unmapped code falls back to the generic resubmission copy.
        user2 = _person(db_session, "vf-mapping2@test.com")
        sid2 = _launch(client, user2)["id"]
        _post_decision(client, _decision_body(_session(db_session, sid2), "resubmission_requested", reason_code=555))
        assert _session(db_session, sid2).reason_codes == ["RESUBMISSION_REQUESTED"]
        bad = client.put("/api/identity-verifications/providers/reason-mappings", json={
            "providerDecision": "resubmission_requested", "providerReasonCode": "1", "zoikoReasonCode": "MADE_UP",
        }, cookies=auth_admin_cookie(admin))
        assert bad.status_code == 400


class TestGoLiveGates:
    """ADR Section 17: production stays off until every gate is confirmed."""

    def test_production_integration_fails_closed_until_all_gates_are_confirmed(self, client, db_session, veriff_pack, monkeypatch):
        from app.models.identity_provider_config import GO_LIVE_GATES

        monkeypatch.setattr(settings, "veriff_integration_id", "production")
        admin = _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user = _person(db_session, "vf-golive@test.com")
        assert client.get(f"{BASE}/policy?country=GB", cookies=auth_user_cookie(user)).json()["providerAvailable"] is False
        assert _launch(client, user)["reasonCodes"] == ["PROVIDER_UNAVAILABLE"]  # never verified, progress kept

        ready = client.get("/api/identity-verifications/providers/veriff/readiness", cookies=auth_admin_cookie(admin)).json()
        assert ready["isProduction"] is True and ready["enabled"] is False and "go-live gates open" in ready["disabledReason"]
        assert client.put("/api/identity-verifications/providers/veriff/gates/DPA_SIGNED", json={"confirmed": True},
                          cookies=auth_admin_cookie(admin)).status_code == 400  # needs evidence
        for code, _label in GO_LIVE_GATES:
            r = client.put(f"/api/identity-verifications/providers/veriff/gates/{code}",
                           json={"confirmed": True, "evidenceReference": f"ticket-{code}"}, cookies=auth_admin_cookie(admin))
            assert r.status_code == 200, r.text
        assert r.json()["enabled"] is True
        assert client.get(f"{BASE}/policy?country=GB", cookies=auth_user_cookie(user)).json()["providerAvailable"] is True

    def test_sandbox_is_not_gated(self, client, db_session, veriff_pack):
        user = _person(db_session, "vf-sandbox@test.com")
        assert _launch(client, user)["captureAvailable"] is True

    def test_connection_test_and_readiness(self, client, db_session, veriff_pack, veriff):
        admin = _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        r = client.post("/api/identity-verifications/providers/veriff/connection-test", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["lastConnectionTest"]["ok"] is True
        assert [c["code"] for c in body["checks"] if c["ok"]][:3] == ["CREDENTIALS", "PLAN", "CONNECTION_TEST"]
        assert body["webhookUrls"]["decision"].endswith("/api/v1/webhooks/veriff/decision")
        assert API_KEY not in r.text and SECRET not in r.text
        assert [c[0] for c in veriff.calls[-2:]] == ["POST", "DELETE"]  # the test session is cleaned up
        veriff.down = True
        failed = client.post("/api/identity-verifications/providers/veriff/connection-test", cookies=auth_admin_cookie(admin)).json()
        assert failed["lastConnectionTest"]["ok"] is False


class TestProductionSafety:
    def test_there_is_no_upload_or_manual_route(self, client, db_session, veriff_pack):
        """Only Veriff decides: a person can't upload a document to Zoiko or
        ask a Zoiko reviewer instead, and the manual method is refused."""
        user = _person(db_session, "vf-nomanual@test.com")
        sid = _launch(client, user)["id"]
        r = client.post(f"{BASE}/verifications/{sid}/document", data={"document_type": "passport"},
                        files={"file": ("id.pdf", b"%PDF-1.4 x", "application/pdf")}, cookies=auth_user_cookie(user))
        assert r.status_code in (404, 405)
        r = client.post(f"{BASE}/verifications/{sid}/alternative", json={"reasonCode": "NO_CAMERA"},
                        cookies=auth_user_cookie(user))
        assert r.status_code in (404, 405)
        r = client.post(f"{BASE}/verifications", json={"method": "MANUAL"}, cookies=auth_user_cookie(user))
        assert r.status_code == 400
        assert client.get(f"{BASE}/policy?country=GB", cookies=auth_user_cookie(user)).json()["availableMethods"] == ["DOCUMENT"]

    def test_an_unconfigured_provider_never_verifies_anyone(self, client, db_session, veriff_pack, monkeypatch):
        """No Veriff = blocked with a message, never a fallback check."""
        monkeypatch.setattr(settings, "veriff_api_key", "")
        user = _person(db_session, "vf-blocked@test.com")
        body = _launch(client, user)
        assert (body["state"], body["reasonCodes"], body["captureAvailable"]) == ("IN_PROGRESS", ["PROVIDER_UNAVAILABLE"], False)
        assert "temporarily unavailable" in body["message"]
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None

    def test_production_startup_refuses_unsafe_identity_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "identity_default_provider", "zoiko_document_check")
        monkeypatch.setattr(settings, "veriff_api_key", "k")
        monkeypatch.setattr(settings, "veriff_shared_secret", "")
        monkeypatch.setattr(settings, "veriff_plan", "premium")
        problems = " | ".join(settings._identity_production_problems())
        assert "IDENTITY_DEFAULT_PROVIDER" in problems
        assert "VERIFF_PLAN" in problems and "set together" in problems
        monkeypatch.setattr(settings, "identity_default_provider", "veriff")
        monkeypatch.setattr(settings, "veriff_shared_secret", "s")
        monkeypatch.setattr(settings, "veriff_plan", "full_auto")
        monkeypatch.setattr(settings, "public_api_url", "http://insecure")
        assert any("PUBLIC_API_URL" in p for p in settings._identity_production_problems())
        monkeypatch.setattr(settings, "public_api_url", "https://api.zoikorooms.com")
        assert settings._identity_production_problems() == []


class TestUserRefresh:
    def test_refresh_asks_veriff_for_the_decision_and_is_rate_limited(self, client, db_session, veriff_pack, veriff):
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user = _person(db_session, "vf-refresh@test.com")
        sid = _launch(client, user)["id"]
        veriff.decision = None
        r = client.post(f"{BASE}/verifications/{sid}/refresh", cookies=auth_user_cookie(user))
        assert (r.status_code, r.json()["state"]) == (200, "IN_PROGRESS")
        veriff.decision = json.loads(_decision_body(_session(db_session, sid), "approved"))["verification"]
        gets = sum(1 for c in veriff.calls if c[0] == "GET")
        assert client.post(f"{BASE}/verifications/{sid}/refresh", cookies=auth_user_cookie(user)).json()["state"] == "IN_PROGRESS"
        assert sum(1 for c in veriff.calls if c[0] == "GET") == gets  # within 30s: no second provider call
        record = _session(db_session, sid)
        record.match_results = {**record.match_results, "user_refreshed_at": "2000-01-01T00:00:00+00:00"}
        db_session.commit()
        assert client.post(f"{BASE}/verifications/{sid}/refresh", cookies=auth_user_cookie(user)).json()["state"] == "VERIFIED"
        other = _person(db_session, "vf-refresh-other@test.com")
        assert client.post(f"{BASE}/verifications/{sid}/refresh", cookies=auth_user_cookie(other)).status_code == 403


class TestOrderingAcrossAttempts:
    """ADR Section 8 ordering safety: compare attempt identifiers, decision
    times and state precedence before applying a decision."""

    @pytest.fixture()
    def launched(self, client, db_session, veriff_pack):
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user = _person(db_session, f"vf-order-{id(self)}@test.com")
        sid = _launch(client, user)["id"]
        return user, _session(db_session, sid)

    def test_a_late_decline_from_an_earlier_attempt_does_not_undo_verification(self, client, db_session, launched):
        user, session = launched
        _post_decision(client, _decision_body(session, "approved", attempt="att-2"))
        _post_decision(client, _decision_body(session, "declined", attempt="att-1"))
        assert _session(db_session, session.id).session_state == "VERIFIED"
        assert identity_service.get_profile(db_session, user.party_id).state == "VERIFIED"

    def test_a_decline_for_the_verified_attempt_still_invalidates_it(self, client, db_session, launched):
        user, session = launched
        _post_decision(client, _decision_body(session, "approved", attempt="att-2"))
        _post_decision(client, _decision_body(session, "declined", attempt="att-2"))
        assert identity_service.get_profile(db_session, user.party_id).state == "REVERIFICATION_REQUIRED"

    def test_a_late_interim_result_from_an_earlier_attempt_is_ignored(self, client, db_session, launched):
        _user, session = launched
        _post_decision(client, _decision_body(session, "review", attempt="att-2"))
        _post_decision(client, _decision_body(session, "resubmission_requested", attempt="att-1"))
        record = _session(db_session, session.id)
        assert (record.session_state, record.provider_decision, record.provider_attempt_id) == ("PROCESSING", "review", "att-2")
        _post_decision(client, _decision_body(session, "approved", attempt="att-2"))
        assert _session(db_session, session.id).session_state == "VERIFIED"

    def test_a_second_resubmission_request_on_a_new_attempt_is_applied(self, client, db_session, launched):
        _user, session = launched
        _post_decision(client, _decision_body(session, "resubmission_requested", attempt="att-1"))
        _post_decision(client, _decision_body(session, "resubmission_requested", attempt="att-2"))
        record = _session(db_session, session.id)
        assert (record.session_state, record.provider_attempt_id) == ("ACTION_REQUIRED", "att-2")

    def test_an_older_decision_time_loses_even_within_one_attempt(self, client, db_session, launched):
        _user, session = launched
        _post_decision(client, _decision_body(session, "review", decisionTime="2099-10-01T10:05:00Z"))
        _post_decision(client, _decision_body(session, "resubmission_requested", decisionTime="2099-10-01T10:00:00Z"))
        assert _session(db_session, session.id).session_state == "PROCESSING"
        _post_decision(client, _decision_body(session, "approved", decisionTime="2099-10-01T10:10:00Z"))
        assert _session(db_session, session.id).session_state == "VERIFIED"


class TestAcknowledgeThenProcess:
    def test_the_webhook_is_acknowledged_after_durable_acceptance_and_processed_afterwards(
        self, client, db_session, veriff_pack, monkeypatch,
    ):
        """ADR Section 8: authenticate, persist, acknowledge -- processing is
        not part of the request. If it never runs, the sweeper applies it."""
        from app.api.routes import user_identity_flow

        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        monkeypatch.setattr(user_identity_flow, "_process_events_later", lambda *a, **k: None)
        user = _person(db_session, "vf-ack@test.com")
        session = _session(db_session, _launch(client, user)["id"])
        r = _post_decision(client, _decision_body(session, "approved"))
        assert (r.status_code, r.json()) == (200, {"accepted": 1, "duplicates": 0})
        assert db_session.query(IdentityProviderEvent).filter_by(status="accepted").count() == 1
        assert _session(db_session, session.id).session_state == "IN_PROGRESS"
        assert identity_service.process_pending_webhook_events(db_session) == 1
        assert _session(db_session, session.id).session_state == "VERIFIED"


class TestWebhookIpControls:
    @pytest.fixture()
    def launched(self, client, db_session, veriff_pack, monkeypatch):
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        monkeypatch.setattr(settings, "veriff_webhook_allowed_ips", "203.0.113.0/24, 198.51.100.7")
        monkeypatch.setattr(settings, "veriff_webhook_trusted_proxy_hops", 1)
        user = _person(db_session, "vf-ip@test.com")
        return _session(db_session, _launch(client, user)["id"])

    def _post(self, client, body, forwarded, signature=None):
        return client.post("/api/v1/webhooks/veriff/decision", content=body, headers={
            "x-auth-client": API_KEY, "x-hmac-signature": signature or _sign(body), "x-forwarded-for": forwarded,
        })

    def test_an_allowed_source_with_a_valid_signature_is_accepted(self, client, db_session, launched):
        r = self._post(client, _decision_body(launched, "approved"), "10.0.0.1, 203.0.113.9")
        assert r.status_code == 200, r.text
        assert _session(db_session, launched.id).session_state == "VERIFIED"

    def test_a_source_outside_the_list_is_refused_before_parsing(self, client, db_session, launched):
        r = self._post(client, _decision_body(launched, "approved"), "192.0.2.50")
        assert r.status_code == 403
        assert db_session.query(IdentityProviderEvent).filter_by(event_type="decision.ip_rejected").count() == 1
        assert _session(db_session, launched.id).session_state == "IN_PROGRESS"

    def test_the_caller_cannot_spoof_an_allowed_address(self, client, db_session, launched):
        # The proxy appends the real peer last; a forged first hop doesn't count.
        r = self._post(client, _decision_body(launched, "approved"), "203.0.113.9, 192.0.2.50")
        assert r.status_code == 403

    def test_ip_filtering_never_replaces_the_hmac_check(self, client, db_session, launched):
        r = self._post(client, _decision_body(launched, "approved"), "198.51.100.7", signature="bad")
        assert r.status_code == 401

    def test_no_list_means_no_ip_filter(self, client, db_session, launched, monkeypatch):
        monkeypatch.setattr(settings, "veriff_webhook_allowed_ips", "")
        assert self._post(client, _decision_body(launched, "approved"), "192.0.2.50").status_code == 200


class TestAdrApiPaths:
    """ADR Section 10: the internal API contract at the paths it names."""

    def test_create_read_and_restart_at_the_v1_paths(self, client, db_session, veriff_pack):
        user = _person(db_session, "vf-v1@test.com")
        r = client.put(f"{BASE}/details", json={"givenName": "Asha", "familyName": "Rao", "dateOfBirth": "1990-05-01",
                                                "countryCode": "GB"}, cookies=auth_user_cookie(user))
        assert r.status_code == 200, r.text
        r = client.post("/api/v1/identity-verifications", json={"method": "DOCUMENT", "roleContext": "OWNER"},
                        cookies=auth_user_cookie(user))
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        client.post(f"{BASE}/verifications/{sid}/submit", json={"attested": True}, cookies=auth_user_cookie(user))
        read = client.get(f"/api/v1/identity-verifications/{sid}", cookies=auth_user_cookie(user)).json()
        assert read["id"] == sid and "providerSessionId" not in read
        _post_decision(client, _decision_body(_session(db_session, sid), "expired"))
        r = client.post(f"/api/v1/identity-verifications/{sid}/restart", cookies=auth_user_cookie(user))
        assert r.status_code == 201 and r.json()["id"] != sid
        assert client.get(f"/api/v1/identity-verifications/{sid}").status_code == 401

    def test_internal_reconcile_is_super_admin_only(self, client, db_session, veriff_pack, veriff):
        super_admin = _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        reviewer = _make_admin(db_session, email="vf-reviewer@test.com", role="admin")
        user = _person(db_session, "vf-internal@test.com")
        sid = _launch(client, user)["id"]
        veriff.decision = json.loads(_decision_body(_session(db_session, sid), "approved"))["verification"]
        path = f"/internal/identity-verifications/{sid}/reconcile"
        assert client.post(path).status_code == 401
        assert client.post(path, cookies=auth_admin_cookie(reviewer)).status_code == 403
        r = client.post(path, cookies=auth_admin_cookie(super_admin))
        assert (r.status_code, r.json()) == (200, {"result": "applied"})
        assert _session(db_session, sid).session_state == "VERIFIED"


class TestProviderCost:
    def test_cost_per_completed_verification_and_per_approved_account(self, client, db_session, veriff_pack, monkeypatch):
        admin = _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        m = client.get("/api/identity-verifications/metrics", cookies=auth_admin_cookie(admin)).json()
        assert m["providerCost"] is None  # no contracted price configured
        monkeypatch.setattr(settings, "veriff_cost_per_session", 1.5)
        approved = _person(db_session, "vf-cost-1@test.com")
        declined = _person(db_session, "vf-cost-2@test.com")
        _post_decision(client, _decision_body(_session(db_session, _launch(client, approved)["id"]), "approved"))
        _post_decision(client, _decision_body(_session(db_session, _launch(client, declined)["id"]), "declined"))
        cost = client.get("/api/identity-verifications/metrics", cookies=auth_admin_cookie(admin)).json()["providerCost"]
        assert cost == {"currency": "EUR", "perSession": 1.5, "sessions": 2, "total": 3.0,
                        "perCompletedVerification": 1.5, "perApprovedAccount": 3.0}


class TestZoikoCapture:
    """The person photographs the document and takes the selfie in Zoiko's
    own screens; each photo is relayed straight to Veriff's media API (never
    stored) and Veriff decides."""

    @pytest.fixture()
    def launched(self, client, db_session, veriff_pack):
        user = _person(db_session, f"vf-cap-{id(self)}@test.com")
        sid = _launch(client, user)["id"]
        return user, sid

    def test_photos_are_relayed_to_veriff_signed_and_never_stored(self, client, db_session, launched, veriff,
                                                                  tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "identity_upload_dir", str(tmp_path))
        user, sid = launched
        photo = _jpeg()
        r = _capture(client, user, sid, "document-front", document_type="passport", content=photo)
        assert r.status_code == 200, r.text
        assert (r.json()["captured"], r.json()["documentType"], r.json()["backRequired"]) == (["document-front"], "passport", False)
        method, url, headers, content = veriff.calls[-1]
        assert (method, url) == ("POST", "https://stationapi.veriff.com/v1/sessions/veriff-session-1/media")
        assert headers["X-AUTH-CLIENT"] == API_KEY and headers["X-HMAC-SIGNATURE"] == _sign(content)
        sent = json.loads(content)["image"]
        assert sent["context"] == "document-front"
        assert base64.b64decode(sent["content"].split(",", 1)[1]) == photo
        record = _session(db_session, sid)
        assert record.document_file_path is None and stored_refs(db_session, "identity_document") == []
        assert photo.hex()[:64] not in json.dumps(record.match_results)

    def test_completing_needs_the_photos_and_then_veriff_decides(self, client, db_session, launched, veriff):
        user, sid = launched
        assert _complete(client, user, sid).status_code == 400
        _capture(client, user, sid, "document-front", document_type="passport")
        r = _complete(client, user, sid)
        assert r.status_code == 400 and "selfie" in r.json()["detail"]
        _capture(client, user, sid, "face")
        r = _complete(client, user, sid)
        assert r.status_code == 200, r.text
        assert (r.json()["state"], r.json()["captureAvailable"]) == ("PROCESSING", False)
        method, url, headers, content = veriff.calls[-1]
        assert (method, url) == ("PATCH", "https://stationapi.veriff.com/v1/sessions/veriff-session-1")
        assert json.loads(content) == {"verification": {"status": "submitted"}}
        assert headers["X-HMAC-SIGNATURE"] == _sign(content)
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None  # only Veriff's decision verifies
        assert _capture(client, user, sid, "face").status_code == 409  # nothing more after submitting

    def test_an_identity_card_needs_its_back_side(self, client, db_session, launched):
        user, sid = launched
        body = _capture(client, user, sid, "document-front", document_type="driving_license").json()
        assert body["backRequired"] is True
        _capture(client, user, sid, "face")
        r = _complete(client, user, sid)
        assert r.status_code == 400 and "back of your document" in r.json()["detail"]
        _capture(client, user, sid, "document-back")
        assert _complete(client, user, sid).json()["state"] == "PROCESSING"

    def test_bad_photos_and_unaccepted_documents_are_refused(self, client, db_session, launched):
        user, sid = launched
        assert _capture(client, user, sid, "document-front", document_type="aadhaar").status_code == 400  # not in GB
        assert _capture(client, user, sid, "document-front", document_type="passport",
                        content=b"%PDF-1.4 not a photo" * 600).status_code == 400
        assert _capture(client, user, sid, "document-front", document_type="passport",
                        content=b"\xff\xd8\xff tiny").status_code == 400
        assert _capture(client, user, sid, "document-back").status_code == 400  # front first
        assert _capture(client, user, sid, "selfie-video").status_code == 400
        other = _person(db_session, "vf-cap-other@test.com")
        assert _capture(client, other, sid, "face").status_code == 403
        assert _session(db_session, sid).match_results.get("captured") in (None, [])

    def test_a_veriff_outage_during_capture_keeps_progress(self, client, db_session, launched, veriff):
        user, sid = launched
        _capture(client, user, sid, "document-front", document_type="passport")
        veriff.down = True
        r = _capture(client, user, sid, "face")
        assert r.status_code == 503 and "temporarily unavailable" in r.json()["detail"]
        assert _session(db_session, sid).match_results["captured"] == ["document-front"]
        assert _complete(client, user, sid).status_code == 400
        veriff.down = False
        _capture(client, user, sid, "face")
        assert _complete(client, user, sid).json()["state"] == "PROCESSING"

    def test_the_whole_flow_ends_verified_only_by_veriffs_decision(self, client, db_session, launched):
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user, sid = launched
        _capture(client, user, sid, "document-front", document_type="passport")
        _capture(client, user, sid, "face")
        _complete(client, user, sid)
        _post_decision(client, _decision_body(_session(db_session, sid), "approved"))
        assert identity_service.get_profile(db_session, user.party_id).state == "VERIFIED"


class TestResubmissionDecisionReplay:
    """Regression (user10): after a resubmission, the decision API still
    returned the previous "resubmission_requested" until Veriff decided on the
    new photos. Re-applying that old decision sent the person back to
    "action required" although Veriff then approved."""

    def _setup(self, client, db_session, veriff_pack):
        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user = _person(db_session, "vf-replay@test.com")
        sid = _launch(client, user)["id"]
        return user, sid

    def _photos(self, client, user, sid):
        _capture(client, user, sid, "document-front", document_type="passport")
        _capture(client, user, sid, "face")
        assert _complete(client, user, sid).json()["state"] == "PROCESSING"

    def test_the_old_decision_is_not_applied_to_the_new_photos(self, client, db_session, veriff_pack, veriff):
        user, sid = self._setup(client, db_session, veriff_pack)
        self._photos(client, user, sid)
        old = json.loads(_decision_body(_session(db_session, sid), "resubmission_requested", attempt="att-1",
                                        decisionTime="2026-10-05T05:20:06Z"))["verification"]
        veriff.decision = old
        assert identity_service.reconcile(db_session, _session(db_session, sid)) == "applied"
        assert _session(db_session, sid).session_state == "ACTION_REQUIRED"

        self._photos(client, user, sid)  # new photos, submitted after the old decision
        # The decision API still returns the old decision -> ignored.
        identity_service.reconcile(db_session, _session(db_session, sid))
        assert _session(db_session, sid).session_state == "PROCESSING"
        # Veriff decides on the new attempt -> verified.
        veriff.decision = {**old, "status": "approved", "attemptId": "att-2", "decisionTime": "2099-01-01T00:00:00Z"}
        identity_service.reconcile(db_session, _session(db_session, sid))
        assert _session(db_session, sid).session_state == "VERIFIED"

    def test_a_session_stuck_in_action_required_still_picks_up_the_approval(self, client, db_session, veriff_pack, veriff):
        user, sid = self._setup(client, db_session, veriff_pack)
        self._photos(client, user, sid)
        veriff.decision = json.loads(_decision_body(_session(db_session, sid), "resubmission_requested", attempt="att-1",
                                                    decisionTime="2026-10-05T05:20:06Z"))["verification"]
        identity_service.reconcile(db_session, _session(db_session, sid))
        assert _session(db_session, sid).session_state == "ACTION_REQUIRED"
        veriff.decision = {**veriff.decision, "status": "approved", "attemptId": "att-2", "decisionTime": "2099-01-01T00:00:00Z"}
        r = client.post(f"{BASE}/verifications/{sid}/refresh", cookies=auth_user_cookie(user))
        assert r.status_code == 200 and r.json()["state"] == "VERIFIED"
