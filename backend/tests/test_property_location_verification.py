"""ZR-PROPERTY-VERIFY-001 -- property & location verification flow: provider
adapter isolation, structured address, controlled pin, unit / duplicates,
evidence, the automated decision, review (incl. four-eyes), invalidation,
expiry, publish gate, concurrency and privacy. Section 20 QA scenarios."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.property_verification import get_valid_property_verification_for_room
from app.models.domain_event import DomainEvent
from app.models.party import Party
from app.models.property import Property
from app.models.property_location import PropertyLocationVerification
from app.models.room import Room
from app.services import location as loc
from app.services import property_location_service as svc
from tests.conftest import stored_refs, _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

BASE = "/api/users/property-verifications"
ADDRESS = {"addressLine1": "2-599 Madupally", "locality": "Madhira", "administrativeArea": "Telangana",
           "postalCode": "507116", "countryCode": "IN"}
LAT, LNG = 16.9200, 80.3700


class FakeProvider:
    """Stands in for the location adapter; records nothing provider-specific."""

    def __init__(self):
        self.status = "VALIDATED"
        self.precision = "ROOFTOP"
        self.geocode_status = "RESOLVED"
        self.down = False
        self.suggestion = None
        self.reverse_postal = "507116"

    def validate(self, address):
        if self.down:
            raise loc.LocationUnavailable("down")
        canonical = loc.CanonicalAddress(**{**address.as_dict(), "formatted": "2-599 Madupally, Madhira, Telangana 507116, India"})
        location = loc.CanonicalLocation(LAT, LNG, self.precision, self.geocode_status, place_id="place-1")
        return loc.ValidationResult(self.status, canonical, self.suggestion, location, provider="fake")

    def geocode(self, address):
        if self.down:
            raise loc.LocationUnavailable("down")
        return loc.CanonicalLocation(LAT, LNG, self.precision, self.geocode_status), "fake"

    def reverse(self, lat, lng):
        if self.down:
            raise loc.LocationUnavailable("down")
        return loc.CanonicalAddress(address_line_1="near", locality="Madhira", postal_code=self.reverse_postal,
                                    country_code="IN", formatted=f"Somewhere {self.reverse_postal}"), "fake"


@pytest.fixture()
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(loc, "validate", fake.validate)
    monkeypatch.setattr(loc, "geocode", fake.geocode)
    monkeypatch.setattr(loc, "reverse", fake.reverse)
    return fake


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "property_location_upload_dir", str(tmp_path))
    return tmp_path


def _host(db: Session, email: str):
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    prop = Property(owner_party_id=party.id, address="2-599 Madupally", city="Madhira", jurisdiction_code="IN",
                    status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=False, status="active")
    db.add(room)
    db.commit()
    return user, prop, room


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


class Flow:
    """Drives the host API step by step."""

    def __init__(self, client, user, prop):
        self.client, self.user, self.prop = client, user, prop
        self.body = None

    @property
    def cookies(self):
        return auth_user_cookie(self.user)

    def _send(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        if self.body is not None:
            headers.setdefault("If-Match", str(self.body["version"]))
        r = self.client.request(method, f"{BASE}{path}", cookies=self.cookies, headers=headers, **kwargs)
        if r.status_code < 300:
            self.body = r.json()
        return r

    def start(self, key="k1"):
        r = self.client.post(BASE, json={"propertyId": self.prop.id}, headers={"Idempotency-Key": key}, cookies=self.cookies)
        assert r.status_code == 201, r.text
        self.body = r.json()
        return r

    def address(self, address=None, mode="MANUAL"):
        return self._send("PUT", f"/{self.body['id']}/address", json={"address": address or ADDRESS, "entryMode": mode})

    def confirm_address(self, use_suggestion=False):
        return self._send("POST", f"/{self.body['id']}/address/confirm", json={"useSuggestion": use_suggestion})

    def location(self, action="confirm", **kwargs):
        return self._send("POST", f"/{self.body['id']}/confirm-location", json={"action": action, **kwargs})

    def unit(self, kind="HOUSE", unit=""):
        return self._send("PUT", f"/{self.body['id']}/unit", json={"propertyKind": kind, "unit": unit})

    def evidence(self, content=None, evidence_type="PROPERTY_TAX_RECORD", name="tax.pdf"):
        content = content if content is not None else _pdf("Property tax record", "2-599 Madupally, Madhira", "PIN 507116")
        return self._send("POST", f"/{self.body['id']}/evidence", data={"evidence_type": evidence_type},
                          files={"file": (name, content, "application/pdf")})

    def submit(self, attested=True, key=None):
        headers = {"Idempotency-Key": key} if key else {}
        return self._send("POST", f"/{self.body['id']}/submit", json={"attested": attested}, headers=headers)

    def happy_path(self, kind="HOUSE", unit="", evidence=None):
        self.start()
        assert self.address().status_code == 200
        assert self.confirm_address().status_code == 200
        assert self.location().status_code == 200
        assert self.unit(kind, unit).status_code == 200, self.body
        assert self.evidence(evidence).status_code == 201
        r = self.submit()
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture()
def host(db_session):
    _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
    return _host(db_session, "plv-host@test.com")


def _flow(client, host):
    user, prop, _room = host
    return Flow(client, user, prop)


class TestExactUrbanAddress:
    def test_validated_rooftop_address_with_matching_evidence_is_verified(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        body = flow.happy_path()
        assert body["state"] == "VERIFIED"
        assert body["pinStatus"] == "AUTO_CONFIRMED" and body["locationConfidence"] == "EXACT"
        v = db_session.get(PropertyLocationVerification, body["id"])
        assert v.existence_status == "EVIDENCE_CONFIRMED" and v.expires_at is not None
        prop = db_session.get(Property, host[1].id)
        db_session.refresh(prop)
        assert (prop.address_line_1, prop.postal_code, prop.country_code) == ("2-599 Madupally", "507116", "IN")
        assert prop.latitude_private == LAT and prop.pin_status == "AUTO_CONFIRMED"
        # The publish gate (Section 2) now passes for the property's rooms.
        assert get_valid_property_verification_for_room(db_session, host[2].id) is not None
        types = [e.event_type for e in db_session.query(DomainEvent).filter_by(resource_type=svc.RESOURCE).all()]
        for expected in ("PROPERTY_VERIFICATION_STARTED", "ADDRESS_MANUALLY_ENTERED", "ADDRESS_VALIDATED", "GEOCODE_RESOLVED",
                         "PIN_CONFIRMED", "PROPERTY_EVIDENCE_UPLOADED", "PROPERTY_SOURCE_CHECKED", "PROPERTY_VERIFIED"):
            assert expected in types

    def test_events_never_carry_coordinates_or_documents(self, client, db_session, host, provider, uploads):
        _flow(client, host).happy_path()
        dump = str([e.payload for e in db_session.query(DomainEvent).filter_by(resource_type=svc.RESOURCE).all()])
        assert str(LAT) not in dump and "Madupally" not in dump


class TestTrustSeparation:
    def test_address_and_map_success_alone_never_verify(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start()
        flow.address()
        flow.confirm_address()
        flow.location()
        flow.unit()
        r = flow.submit()
        assert r.status_code == 400 and "evidence" in r.json()["detail"]
        assert get_valid_property_verification_for_room(db_session, host[2].id) is None

    def test_a_utility_bill_alone_needs_a_primary_document(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start()
        flow.address(); flow.confirm_address(); flow.location(); flow.unit()
        flow.evidence(evidence_type="UTILITY_BILL")
        body = flow.submit().json()
        assert body["state"] == "ACTION_REQUIRED" and "SUPPLEMENTARY_EVIDENCE_ONLY" in body["reasonCodes"]

    def test_unreadable_evidence_asks_for_a_readable_copy(self, client, db_session, host, provider, uploads):
        from PIL import Image

        image = io.BytesIO()
        Image.new("RGB", (300, 200), "white").save(image, format="PNG")
        flow = _flow(client, host)
        flow.start()
        flow.address(); flow.confirm_address(); flow.location(); flow.unit()
        r = flow._send("POST", f"/{flow.body['id']}/evidence", data={"evidence_type": "PROPERTY_TAX_RECORD"},
                       files={"file": ("photo.png", image.getvalue(), "image/png")})
        assert r.status_code == 201, r.text
        assert flow.submit().json()["state"] == "ACTION_REQUIRED"  # host asked for a readable copy

    def test_evidence_naming_another_address_needs_action(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        body = flow.happy_path(evidence=_pdf("Tax record", "99 Somewhere Else Road, Mumbai 400001"))
        assert body["state"] == "ACTION_REQUIRED" and "EVIDENCE_MISMATCH" in body["reasonCodes"]
        assert "do not clearly match" in body["message"]


class TestApartmentAndUnits:
    def test_apartment_needs_a_unit(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start(); flow.address(); flow.confirm_address(); flow.location()
        r = flow.unit("APARTMENT", "")
        assert r.status_code == 400 and "unit number" in r.json()["detail"]

    def test_unit_not_in_evidence_needs_action(self, client, db_session, host, provider, uploads):
        body = _flow(client, host).happy_path("APARTMENT", "8B")
        assert body["state"] == "ACTION_REQUIRED" and "UNIT_NOT_CONFIRMED" in body["reasonCodes"]

    def test_same_building_different_units_are_not_duplicates(self, client, db_session, host, provider, uploads):
        a = _flow(client, host).happy_path("APARTMENT", "8B", evidence=_pdf("Property tax record", "Flat 8B", "2-599 Madupally, Madhira 507116"))
        assert a["state"] == "VERIFIED"
        other = _host(db_session, "plv-host-2@test.com")
        b = Flow(client, other[0], other[1]).happy_path("APARTMENT", "9C", evidence=_pdf("Property tax record", "Flat 9C", "2-599 Madupally, Madhira 507116"))
        assert b["state"] == "VERIFIED" and not b["possibleDuplicate"]

    def test_the_same_unit_claimed_by_another_account_is_a_duplicate(self, client, db_session, host, provider, uploads):
        _flow(client, host).happy_path("APARTMENT", "8B", evidence=_pdf("Property tax record", "Flat 8B", "2-599 Madupally, Madhira 507116"))
        other = _host(db_session, "plv-host-3@test.com")
        b = Flow(client, other[0], other[1]).happy_path("APARTMENT", "8B", evidence=_pdf("Property tax record", "Flat 8B", "2-599 Madupally Madhira 507116 copy"))
        assert b["state"] == "ACTION_REQUIRED" and b["possibleDuplicate"] and "POSSIBLE_DUPLICATE" in b["reasonCodes"]


class TestMapControl:
    def test_a_10m_adjustment_is_accepted_and_recorded(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start(); flow.address(); flow.confirm_address()
        r = flow.location("adjust", latitude=LAT + 0.00009, longitude=LNG, reason="Marker was on the road, not the entrance")
        body = r.json()
        assert body["pinStatus"] == "ADJUSTED" and "pinMovedMeters" not in body  # thresholds stay internal
        v = db_session.get(PropertyLocationVerification, body["id"])
        assert 5 < v.pin_moved_meters < 15
        assert (v.original_latitude, v.original_longitude) == (LAT, LNG)  # never overwritten
        assert len(v.pin_adjust_history) == 1 and v.pin_adjust_history[0]["reason"].startswith("Marker was")

    def test_a_2km_drag_must_be_corrected(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start(); flow.address(); flow.confirm_address()
        body = flow.location("adjust", latitude=LAT + 0.018, longitude=LNG, reason="moved").json()
        assert body["pinStatus"] == "REVIEW_REQUIRED"
        assert db_session.get(PropertyLocationVerification, body["id"]).pin_moved_meters > 1900
        flow.unit(); flow.evidence()
        body = flow.submit().json()
        assert body["state"] == "ACTION_REQUIRED" and "PIN_MOVED_TOO_FAR" in body["reasonCodes"]

    def test_a_small_move_onto_another_postcode_needs_review(self, client, db_session, host, provider, uploads):
        provider.reverse_postal = "507999"
        flow = _flow(client, host)
        flow.start(); flow.address(); flow.confirm_address()
        body = flow.location("adjust", latitude=LAT + 0.0001, longitude=LNG, reason="entrance").json()
        assert body["pinStatus"] == "REVIEW_REQUIRED"

    def test_an_adjustment_needs_a_reason(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start(); flow.address(); flow.confirm_address()
        assert flow.location("adjust", latitude=LAT, longitude=LNG).status_code == 400


class TestAddressIntegrity:
    def test_wrong_country_is_refused(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start()
        r = flow.address({**ADDRESS, "countryCode": "GB"})
        assert r.status_code == 422

    def test_required_fields_come_from_the_country_pack(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start()
        r = flow.address({**ADDRESS, "postalCode": ""})
        assert r.status_code == 400 and "postal code" in r.json()["detail"]

    def test_a_suggested_correction_can_be_accepted(self, client, db_session, host, provider, uploads):
        provider.suggestion = loc.CanonicalAddress(address_line_1="2-599 Madupally", locality="Madhira",
                                                   administrative_area="Telangana", postal_code="507117", country_code="IN")
        flow = _flow(client, host)
        flow.start()
        body = flow.address().json()
        assert body["suggestedAddress"]["postalCode"] == "507117"
        body = flow.confirm_address(use_suggestion=True).json()
        assert body["canonicalAddress"]["postalCode"] == "507117" and body["addressConfirmed"]


class TestRuralAndOutage:
    def test_provider_outage_keeps_progress_and_the_document_decides(self, client, db_session, host, provider, uploads):
        provider.down = True
        flow = _flow(client, host)
        flow.start()
        body = flow.address().json()
        assert body["addressStatus"] == "UNRESOLVED" and "PROVIDER_UNAVAILABLE" in body["reasonCodes"]
        flow.confirm_address()
        assert flow.location("confirm").status_code == 409  # nothing to confirm -- place it by hand
        body = flow.location("adjust", latitude=LAT, longitude=LNG, reason="Placed by hand").json()
        assert body["pinStatus"] == "REVIEW_REQUIRED"
        flow.unit(); flow.evidence()
        assert flow.submit().json()["state"] == "VERIFIED"  # the document confirms it -- no admin wait

    def test_approximate_geocode_is_verified_by_the_document(self, client, db_session, host, provider, uploads):
        provider.precision, provider.status = "APPROXIMATE", "PARTIAL"
        body = _flow(client, host).happy_path()
        assert body["state"] == "VERIFIED"  # approximate map result, but the document matches


class TestReview:
    def _in_review(self, client, db_session, host, provider):
        """The automatic decision never waits for a reviewer; the console
        still handles cases put into review (e.g. older ones)."""
        body = _flow(client, host).happy_path(evidence=_pdf("Unrelated text"))
        v = db_session.get(PropertyLocationVerification, body["id"])
        v.state = "MANUAL_REVIEW"
        db_session.commit()
        return body

    def test_reviewer_approves_with_a_reason_code(self, client, db_session, host, provider, uploads):
        body = self._in_review(client, db_session, host, provider)
        admin = _make_admin(db_session, email="plv-reviewer@test.com", role="super_admin")
        case = client.get(f"/api/property-verifications/{body['id']}/case", cookies=auth_admin_cookie(admin)).json()
        assert case["state"] == "MANUAL_REVIEW" and case["hostIdentityVerified"] is False
        r = client.post(f"/api/property-verifications/{body['id']}/review", cookies=auth_admin_cookie(admin),
                        json={"decision": "APPROVE", "reasonCode": "BECAUSE"})
        assert r.status_code == 400
        r = client.post(f"/api/property-verifications/{body['id']}/review", cookies=auth_admin_cookie(admin),
                        json={"decision": "APPROVE", "reasonCode": "REVIEW_EVIDENCE_CORROBORATED"})
        assert r.status_code == 200 and r.json()["state"] == "VERIFIED"
        assert db_session.get(PropertyLocationVerification, body["id"]).existence_status == "MANUAL_CONFIRMED"

    def test_request_info_and_reject(self, client, db_session, host, provider, uploads):
        body = self._in_review(client, db_session, host, provider)
        admin = _make_admin(db_session, email="plv-reviewer-2@test.com", role="super_admin")
        r = client.post(f"/api/property-verifications/{body['id']}/review", cookies=auth_admin_cookie(admin),
                        json={"decision": "REQUEST_INFO", "reasonCode": "REVIEW_MORE_EVIDENCE"})
        assert r.json()["state"] == "ACTION_REQUIRED" and r.json()["cta"] == "Add evidence"

    def test_duplicates_need_two_different_reviewers(self, client, db_session, host, provider, uploads):
        _flow(client, host).happy_path("APARTMENT", "8B", evidence=_pdf("Property tax record", "Flat 8B", "2-599 Madupally, Madhira 507116"))
        other = _host(db_session, "plv-dup-host@test.com")
        dup = Flow(client, other[0], other[1]).happy_path("APARTMENT", "8B", evidence=_pdf("Flat 8B", "2-599 Madupally Madhira 507116 v2"))
        v = db_session.get(PropertyLocationVerification, dup["id"])
        v.state = "MANUAL_REVIEW"
        db_session.commit()
        first = _make_admin(db_session, email="plv-first@test.com", role="super_admin")
        second = _make_admin(db_session, email="plv-second@test.com", role="super_admin")
        url = f"/api/property-verifications/{dup['id']}/review"
        body = {"decision": "APPROVE", "reasonCode": "REVIEW_EVIDENCE_CORROBORATED"}
        assert client.post(url, json=body, cookies=auth_admin_cookie(first)).json()["state"] == "MANUAL_REVIEW"
        assert client.post(url, json=body, cookies=auth_admin_cookie(first)).status_code == 409
        assert client.post(url, json=body, cookies=auth_admin_cookie(second)).json()["state"] == "VERIFIED"

    def test_reviews_are_super_admin_only(self, client, db_session, host, provider, uploads):
        body = self._in_review(client, db_session, host, provider)
        plain = _make_admin(db_session, email="plv-plain@test.com", role="admin")
        r = client.post(f"/api/property-verifications/{body['id']}/review", cookies=auth_admin_cookie(plain),
                        json={"decision": "APPROVE", "reasonCode": "REVIEW_EVIDENCE_CORROBORATED"})
        assert r.status_code == 403


class TestInvalidationExpiryConcurrency:
    def test_a_material_address_edit_invalidates_and_keeps_history(self, client, db_session, host, provider, uploads):
        body = _flow(client, host).happy_path()
        user, prop, room = host
        db_session.refresh(prop)
        stale = client.put(f"/api/users/hosting/properties/{prop.id}", cookies=auth_user_cookie(user),
                           json={"address": "77 New Street", "city": "Madhira", "jurisdictionCode": "IN"})
        assert stale.status_code == 428  # a confirmed address needs the version the client read
        r = client.put(f"/api/users/hosting/properties/{prop.id}", cookies=auth_user_cookie(user),
                       headers={"If-Match": str(prop.location_version)},
                       json={"address": "77 New Street", "city": "Madhira", "jurisdictionCode": "IN"})
        assert r.status_code == 200, r.text
        v = db_session.get(PropertyLocationVerification, body["id"])
        db_session.refresh(v)
        assert v.state == "INVALIDATED" and v.reason_codes == ["ADDRESS_CHANGED"]
        assert get_valid_property_verification_for_room(db_session, room.id) is None
        status_body = client.get(f"{BASE}/properties/{prop.id}", cookies=auth_user_cookie(user)).json()
        assert status_body["state"] == "INVALIDATED" and status_body["verification"]["canRestart"]

    def test_expiry_sweep_and_expiring_soon(self, client, db_session, host, provider, uploads):
        body = _flow(client, host).happy_path()
        v = db_session.get(PropertyLocationVerification, body["id"])
        v.expires_at = datetime.now(timezone.utc) + timedelta(days=10)
        db_session.commit()
        assert svc.effective_state(v) == "EXPIRING_SOON"
        v.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        assert svc.sweep_expired(db_session) == 1
        db_session.refresh(v)
        assert v.state == "EXPIRED"

    def test_a_stale_client_gets_409(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start()
        stale = flow.body["version"]
        flow.address()
        r = flow._send("POST", f"/{flow.body['id']}/address/confirm", json={}, headers={"If-Match": str(stale)})
        assert r.status_code == 409

    def test_start_and_submit_are_idempotent(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start("same")
        first = flow.body["id"]
        assert flow.start("same").json()["id"] == first
        flow.address(); flow.confirm_address(); flow.location(); flow.unit(); flow.evidence()
        a = flow.submit(key="sub-1").json()
        b = flow._send("POST", f"/{first}/submit", json={"attested": True}, headers={"Idempotency-Key": "sub-1",
                                                                                     "If-Match": "1"}).json()
        assert a["state"] == b["state"] == "VERIFIED"

    def test_other_hosts_cannot_touch_a_verification(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start()
        intruder = _host(db_session, "plv-intruder@test.com")[0]
        r = client.get(f"{BASE}/{flow.body['id']}", cookies=auth_user_cookie(intruder))
        assert r.status_code == 403


class TestEvidenceSecurity:
    def test_evidence_is_encrypted_and_reuse_is_flagged(self, client, db_session, host, provider, uploads):
        doc = _pdf("Tax record", "2-599 Madupally, Madhira 507116")
        _flow(client, host).happy_path(evidence=doc)
        from app.core import file_store

        stored = stored_refs(db_session, "property_location")
        assert stored and all(ref.endswith(".enc") for ref in stored)
        assert all(b"Madupally" not in file_store.read(db_session, "property_location", ref) for ref in stored)
        other = _host(db_session, "plv-reuse@test.com")
        body = Flow(client, other[0], other[1]).happy_path(evidence=doc)
        assert body["state"] == "ACTION_REQUIRED" and "EVIDENCE_REUSED" in body["reasonCodes"]

    def test_unaccepted_evidence_types_are_refused(self, client, db_session, host, provider, uploads):
        flow = _flow(client, host)
        flow.start(); flow.address(); flow.confirm_address(); flow.location(); flow.unit()
        r = flow.evidence(evidence_type="OTHER_OFFICIAL_DOCUMENT")  # not a Section 9 evidence class
        assert r.status_code == 400


class TestLocationApiAndPrivacy:
    def test_the_location_api_is_provider_neutral_and_needs_sign_in(self, client, db_session, host, provider):
        user = host[0]
        assert client.post("/api/location/validate-address", json={"address": ADDRESS}).status_code == 401
        r = client.post("/api/location/validate-address", json={"address": ADDRESS}, cookies=auth_user_cookie(user))
        assert r.status_code == 200 and r.json()["status"] == "VALIDATED"
        r = client.post("/api/location/geocode", json={"address": ADDRESS}, cookies=auth_user_cookie(user))
        assert r.json()["confidence"] == "EXACT" and "precision" not in r.json()
        caps = client.get("/api/location/capabilities", cookies=auth_user_cookie(user)).json()
        assert "key" not in str(caps).lower()

    def test_suggestions_are_throttled(self, client, db_session, host, monkeypatch):
        from app.api.routes import property_location as routes

        monkeypatch.setattr(routes, "_SUGGEST_LIMIT", 2)
        routes._suggest_hits.clear()
        user = host[0]
        for _ in range(2):
            assert client.post("/api/location/suggestions", json={"query": "madu"}, cookies=auth_user_cookie(user)).status_code == 200
        assert client.post("/api/location/suggestions", json={"query": "madu"}, cookies=auth_user_cookie(user)).status_code == 429
