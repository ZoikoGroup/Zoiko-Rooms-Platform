"""ZR-IDV-ADR-001 -- Veriff Document + Selfie IDV behind the Zoiko provider
abstraction. Covers Section 16 acceptance criterion 15: approved, review,
resubmission_requested, declined, expired, abandoned, duplicate webhook,
invalid HMAC, stale session and provider outage -- plus out-of-order
delivery, the event webhook never verifying, and credentials never
reaching the client."""

from __future__ import annotations

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
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

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


def _session(db: Session, session_id: int) -> IdentityVerification:
    db.expire_all()
    return db.get(IdentityVerification, session_id)


class TestSessionCreation:
    def test_the_backend_creates_the_session_and_the_client_gets_only_the_url(self, client, db_session, veriff_pack, veriff):
        user = _person(db_session, "vf-create@test.com")
        policy_body = client.get(f"{BASE}/policy?country=GB", cookies=auth_user_cookie(user)).json()
        assert (policy_body["captureMode"], policy_body["selfieCheck"]) == ("PROVIDER_HOSTED", True)

        body = _launch(client, user)
        assert body["state"] == "IN_PROGRESS"
        assert body["launchUrl"] == f"{VERIFF_URL}-1"
        dump = json.dumps(body)
        assert API_KEY not in dump and SECRET not in dump and "legacy-token" not in dump

        method, url, headers, content = veriff.calls[-1]
        assert method == "POST" and url == "https://stationapi.veriff.com/v1/sessions"
        assert headers["X-AUTH-CLIENT"] == API_KEY
        sent = json.loads(content)["verification"]
        assert sent["vendorData"] == f"zr-idv-{body['id']}"  # opaque Zoiko reference only
        assert "Asha" not in json.dumps(sent) and user.email not in json.dumps(sent)
        assert sent["callback"].endswith("/account/identity?verification=returned")

        record = _session(db_session, body["id"])
        assert record.provider_session_id == "veriff-session-1"
        assert record.provider_session_url_encrypted and VERIFF_URL not in record.provider_session_url_encrypted
        assert record.consent_notice_version.startswith("GB:v")
        assert record.document_file_path is None  # no raw media copied into Zoiko

    def test_the_url_can_be_reopened_while_capture_is_open(self, client, db_session, veriff_pack):
        user = _person(db_session, "vf-relaunch@test.com")
        sid = _launch(client, user)["id"]
        r = client.post(f"{BASE}/verifications/{sid}/launch", cookies=auth_user_cookie(user))
        assert (r.status_code, r.json()["launchUrl"]) == (200, f"{VERIFF_URL}-1")
        other = _person(db_session, "vf-relaunch-other@test.com")
        assert client.post(f"{BASE}/verifications/{sid}/launch", cookies=auth_user_cookie(other)).status_code == 403

    def test_provider_outage_keeps_progress_and_never_verifies(self, client, db_session, veriff_pack, veriff):
        veriff.down = True
        user = _person(db_session, "vf-outage@test.com")
        body = _launch(client, user)
        assert (body["state"], body["reasonCodes"]) == ("IN_PROGRESS", ["PROVIDER_UNAVAILABLE"])
        assert body["launchUrl"] is None
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None
        veriff.down = False  # and it works on retry
        r = client.post(f"{BASE}/verifications/{body['id']}/submit", json={"attested": True}, cookies=auth_user_cookie(user))
        assert r.json()["launchUrl"] == f"{VERIFF_URL}-1"

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
        assert r.json() == {"accepted": 1, "duplicates": 0, "processed": 1}
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

    def test_resubmission_requested_needs_action_and_can_be_relaunched(self, client, db_session, launched):
        user, session = launched
        _post_decision(client, _decision_body(session, "resubmission_requested", reason_code=204))
        record = _session(db_session, session.id)
        assert (record.session_state, record.reason_codes) == ("ACTION_REQUIRED", ["DOCUMENT_UNREADABLE"])
        r = client.post(f"{BASE}/verifications/{session.id}/launch", cookies=auth_user_cookie(user))
        assert r.status_code == 200 and r.json()["launchUrl"]
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
        assert (r["canRestart"], r["launchAvailable"]) == (True, False)

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
        assert _post_decision(client, body).json()["processed"] == 1
        r = _post_decision(client, body)
        assert (r.status_code, r.json()) == (200, {"accepted": 0, "duplicates": 1, "processed": 0})
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
        assert submitted.json()["launchUrl"] == f"{VERIFF_URL}-2"

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
        assert r.status_code == 200 and r.json()["processed"] == 1
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
        assert _launch(client, user)["launchUrl"]

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
    def test_builtin_check_never_verifies_on_its_own_when_not_allowed(self, client, db_session, monkeypatch, tmp_path):
        """In production the built-in check's 'pass' goes to a reviewer."""
        from tests.test_identity_global_flow import _pdf

        monkeypatch.setattr(settings, "identity_upload_dir", str(tmp_path))
        monkeypatch.setattr(settings, "identity_builtin_check_can_verify", False)
        user = _person(db_session, "vf-builtin@test.com")
        client.put(f"{BASE}/details", json={"givenName": "Asha", "familyName": "Rao", "dateOfBirth": "1990-05-01",
                                            "countryCode": "GB"}, cookies=auth_user_cookie(user))
        sid = client.post(f"{BASE}/verifications", json={"method": "DOCUMENT"}, cookies=auth_user_cookie(user)).json()["id"]
        client.post(f"{BASE}/verifications/{sid}/document", data={"document_type": "passport", "document_number": "P1234567"},
                    files={"file": ("id.pdf", _pdf("Name: Asha Rao", "Passport No: P1234567"), "application/pdf")},
                    cookies=auth_user_cookie(user))
        body = client.post(f"{BASE}/verifications/{sid}/submit", json={"attested": True}, cookies=auth_user_cookie(user)).json()
        assert body["state"] == "PENDING_REVIEW"
        assert _session(db_session, sid).reason_codes == ["AUTOMATED_CHECK_INSUFFICIENT"]
        assert idv_crud.get_verified_identity_for_party(db_session, user.party_id) is None

    def test_production_startup_refuses_unsafe_identity_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "identity_builtin_check_can_verify", True)
        monkeypatch.setattr(settings, "veriff_api_key", "k")
        monkeypatch.setattr(settings, "veriff_shared_secret", "")
        monkeypatch.setattr(settings, "veriff_plan", "premium")
        problems = " | ".join(settings._identity_production_problems())
        assert "IDENTITY_BUILTIN_CHECK_CAN_VERIFY" in problems
        assert "VERIFF_PLAN" in problems and "set together" in problems
        monkeypatch.setattr(settings, "identity_builtin_check_can_verify", None)
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
