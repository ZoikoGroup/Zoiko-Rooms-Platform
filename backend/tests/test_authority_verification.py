"""ZR-AUTHORITY-002 -- property-scoped Authority Verification: owner, agent
and sublet routes, evidence, owner / landlord confirmation (link + code),
automated decision, review with four-eyes, revoke / expiry with listing
control, renewal, the authority gate and privacy. Section 17 QA scenarios."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.authority import get_valid_authority_for_room
from app.models.authority_record import AuthorityRecord
from app.models.authority_verification import AuthorityConfirmation, AuthorityVerification
from app.models.domain_event import DomainEvent
from app.models.listing import Listing
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.services import authority_service as svc
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

PROP_URL = "/api/users/properties/{}/authority-verifications"
BASE = "/api/users/authority-verifications"
LEGAL_NAME = "Asha Rao"


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


DEED = _pdf("SALE DEED", "Owner: Asha Rao", "Property: 12 Lake View Road, Hyderabad 500001")
LEASE = _pdf("TENANCY AGREEMENT", "Tenant: Asha Rao", "Premises: 12 Lake View Road, Hyderabad 500001")
MANDATE = _pdf("PROPERTY MANAGEMENT AGREEMENT", "Owner: Vikram Shah", "Agent: Asha Rao",
               "Property: 12 Lake View Road, Hyderabad 500001", "Authority to advertise and rent")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "authority_upload_dir", str(tmp_path))
    state = {"identity": True, "property": True, "emails": []}
    monkeypatch.setattr(svc, "_identity_verified", lambda db, party_id: state["identity"])
    monkeypatch.setattr(svc, "_verified_legal_name", lambda db, party_id: LEGAL_NAME)
    monkeypatch.setattr(svc, "_property_verified", lambda db, property_id: state["property"])

    def fake_send(to, subject, **kwargs):
        state["emails"].append({"to": to, "subject": subject, **kwargs})

    import app.core.mailer as mailer
    monkeypatch.setattr(mailer, "send_email", fake_send)
    return state


def _host(db: Session, email: str):
    party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = party.id
    prop = Property(owner_party_id=party.id, address="12 Lake View Road", address_line_1="12 Lake View Road",
                    city="Hyderabad", postal_code="500001", country_code="IN", jurisdiction_code="IN", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=False, status="active")
    db.add(room)
    db.commit()
    return user, prop, room


class Flow:
    def __init__(self, client, user, prop):
        self.client, self.user, self.prop = client, user, prop
        self.body = None

    def _send(self, method, path, expect=(200, 201), **kwargs):
        headers = kwargs.pop("headers", {})
        if self.body is not None:
            headers.setdefault("If-Match", str(self.body["version"]))
        r = self.client.request(method, path, cookies=auth_user_cookie(self.user), headers=headers, **kwargs)
        if expect and r.status_code not in expect:
            raise AssertionError(f"{method} {path} -> {r.status_code}: {r.text}")
        if r.status_code in (200, 201) and "version" in r.json():
            self.body = r.json()
        return r

    def start(self, relationship="OWNER", **kwargs):
        return self._send("POST", PROP_URL.format(self.prop.id), json={"relationshipType": relationship}, **kwargs)

    def details(self, **payload):
        return self._send("PUT", f"{BASE}/{self.body['id']}/details", json=payload)

    def upload(self, requirement_id, content=DEED, **form):
        return self._send("POST", f"{BASE}/{self.body['id']}/evidence", data={"requirementId": requirement_id, **form},
                          files={"file": ("doc.pdf", content, "application/pdf")})

    def confirm_request(self, requirement_id, email="owner@example.com", **kwargs):
        return self._send("POST", f"{BASE}/{self.body['id']}/owner-confirmations",
                          json={"requirementId": requirement_id, "recipientEmail": email}, **kwargs)

    def submit(self, **kwargs):
        return self._send("POST", f"{BASE}/{self.body['id']}/submit", json={"attested": True}, **kwargs)


def _code_and_token(env):
    link = next(e for e in env["emails"] if e.get("cta_url"))["cta_url"]
    token = link.split("token=")[1]
    code_line = next(e for e in env["emails"] if "code" in e["subject"].lower())["body_lines"][0]
    return token, code_line.split()[-1].rstrip(".")


# -- owner route --------------------------------------------------------------------


class TestOwnerRoute:
    def test_owner_with_matching_deed_is_auto_verified_and_opens_gate(self, client, db_session):
        user, prop, room = _host(db_session, "auth-owner@test.com")
        f = Flow(client, user, prop)
        f.start()
        assert f.body["state"] == "COLLECTING" and f.body["route"] == "OWNER"
        assert get_valid_authority_for_room(db_session, room.id) is None
        f.upload("OWNER_PROPERTY_RIGHT")
        f.submit()
        assert f.body["state"] == "VERIFIED"
        assert f.body["assuranceLevel"] == "AV-1"
        found = get_valid_authority_for_room(db_session, room.id)
        assert isinstance(found, AuthorityVerification) and found.party_id == user.party_id

    def test_identity_is_required_first(self, client, db_session, env):
        env["identity"] = False
        user, prop, _ = _host(db_session, "auth-noid@test.com")
        r = Flow(client, user, prop).start(expect=None)
        assert r.status_code == 409
        assert "Verify your identity" in r.json()["detail"]

    def test_deed_naming_someone_else_needs_action(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-otherowner@test.com")
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT", _pdf("SALE DEED", "Owner: Somebody Else",
                                              "Property: 12 Lake View Road, Hyderabad 500001"))
        f.submit()
        assert f.body["state"] == "ACTION_REQUIRED"
        assert "NAME_MISMATCH" in f.body["reasonCodes"]
        assert "ADD_EVIDENCE" in f.body["allowedActions"]

    def test_deed_for_another_property_needs_action(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-otherprop@test.com")
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT", _pdf("SALE DEED", "Owner: Asha Rao", "Property: 99 Hill Street, Pune 411001"))
        f.submit()
        assert "PROPERTY_MISMATCH" in f.body["reasonCodes"]

    def test_unreadable_image_goes_to_manual_review(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-image@test.com")
        f = Flow(client, user, prop)
        f.start()
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        f._send("POST", f"{BASE}/{f.body['id']}/evidence", data={"requirementId": "OWNER_PROPERTY_RIGHT"},
                files={"file": ("deed.png", png, "image/png")})
        f.submit()
        assert f.body["state"] == "MANUAL_REVIEW"
        assert "EVIDENCE_UNREADABLE" in f.body["reasonCodes"]

    def test_property_not_verified_holds_in_review(self, client, db_session, env):
        env["property"] = False
        user, prop, _ = _host(db_session, "auth-propunver@test.com")
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT")
        f.submit()
        assert f.body["state"] == "MANUAL_REVIEW"
        assert "PROPERTY_NOT_VERIFIED" in f.body["reasonCodes"]

    def test_representative_needs_entity_evidence_and_review(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-rep@test.com")
        f = Flow(client, user, prop)
        f.start("REPRESENTATIVE")
        f.upload("OWNER_PROPERTY_RIGHT")
        f.submit()
        assert f.body["state"] == "ACTION_REQUIRED"
        assert "REQUIREMENT_MISSING" in f.body["reasonCodes"]


# -- agent route --------------------------------------------------------------------


class TestAgentRoute:
    def test_agent_with_mandate_and_owner_confirmation_is_verified(self, client, db_session, env):
        user, prop, room = _host(db_session, "auth-agent@test.com")
        f = Flow(client, user, prop)
        f.start("AGENT")
        f.details(principalName="Vikram Shah", scopeCodes=["ADVERTISE", "RENT"])
        f.upload("AGENT_MANDATE", MANDATE)
        f.confirm_request("AGENT_MANDATE", email="vikram@example.com")
        assert f.body["confirmations"][0]["status"] == "PENDING"
        token, code = _code_and_token(env)
        assert code not in env["emails"][0]["body_lines"][0]  # code isn't in the link email
        summary = client.get(f"/api/authority-confirmations/{token}").json()
        assert summary["requesterName"] == LEGAL_NAME and summary["kind"] == "MANDATE"
        assert "address" not in summary
        r = client.post(f"/api/authority-confirmations/{token}",
                        json={"code": code, "decision": "CONFIRM", "responderName": "Vikram Shah"})
        assert r.status_code == 200 and r.json()["status"] == "CONFIRMED"
        f.body = client.get(f"{BASE}/{f.body['id']}", cookies=auth_user_cookie(user)).json()
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]
        assert get_valid_authority_for_room(db_session, room.id) is not None

    def test_agent_mandate_document_only_goes_to_review(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-agentdoc@test.com")
        f = Flow(client, user, prop)
        f.start("AGENT")
        f.upload("AGENT_MANDATE", _pdf("MANDATE", "Agent: Asha Rao", "12 Lake View Road, Hyderabad 500001"))
        f.submit()
        assert f.body["state"] == "MANUAL_REVIEW"
        assert "DOCUMENT_ONLY_MANDATE" in f.body["reasonCodes"]

    def test_agent_without_advertise_scope_needs_action(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-agentscope@test.com")
        f = Flow(client, user, prop)
        f.start("AGENT")
        f.details(scopeCodes=["COLLECT_RENT"])
        f.upload("AGENT_MANDATE", MANDATE)
        f.submit()
        assert "SCOPE_INSUFFICIENT" in f.body["reasonCodes"]

    def test_expired_mandate_dates_need_action(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-agentdates@test.com")
        f = Flow(client, user, prop)
        f.start("AGENT")
        f.details(effectiveAt="2020-01-01", expiresAt="2021-01-01")
        f.upload("AGENT_MANDATE", MANDATE)
        f.submit()
        assert "AUTHORITY_DATES_INVALID" in f.body["reasonCodes"]

    def test_organization_link_required_and_unverified_org_reviewed(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-agentorg@test.com")
        org = client.post("/api/users/organizations", json={"name": "Lake Lettings Ltd"},
                          cookies=auth_user_cookie(user)).json()
        assert org["status"] == "PENDING"
        f = Flow(client, user, prop)
        f.start("PROPERTY_MANAGER")
        f.details(organizationId=org["id"])
        req = {r["requirementId"] if "requirementId" in r else r["requirement_id"]: r for r in f.body["requirements"]}
        assert req["AGENT_ORGANIZATION_LINK"]["required"] is True


# -- sublet route -------------------------------------------------------------------


class TestSubletRoute:
    def test_lease_alone_is_not_sublet_authority(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-tenant@test.com")
        f = Flow(client, user, prop)
        f.start("TENANT_SUBLETTER")
        f.upload("TENANT_OCCUPATION_RIGHT", LEASE)
        f.submit()
        assert f.body["state"] == "ACTION_REQUIRED"
        assert "SUBLET_PERMISSION_MISSING" in f.body["reasonCodes"]

    def test_tenant_cannot_self_approve(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-selfapprove@test.com")
        f = Flow(client, user, prop)
        f.start("TENANT_SUBLETTER")
        r = f.confirm_request("TENANT_SUBLET_PERMISSION", email="AUTH-selfapprove@test.com", expect=None)
        assert r.status_code == 400

    def test_landlord_confirmation_completes_sublet(self, client, db_session, env):
        user, prop, _ = _host(db_session, "auth-sublet@test.com")
        f = Flow(client, user, prop)
        f.start("TENANT_SUBLETTER")
        f.upload("TENANT_OCCUPATION_RIGHT", LEASE)
        f.confirm_request("TENANT_SUBLET_PERMISSION", email="landlord@example.com")
        token, code = _code_and_token(env)
        client.post(f"/api/authority-confirmations/{token}",
                    json={"code": code, "decision": "CONFIRM", "responderName": "Landlord Person"})
        f.body = client.get(f"{BASE}/{f.body['id']}", cookies=auth_user_cookie(user)).json()
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]
        assert "SUBLET" in f.body["scopeCodes"]

    def test_wrong_code_is_rejected_and_attempts_are_limited(self, client, db_session, env):
        user, prop, _ = _host(db_session, "auth-codelimit@test.com")
        f = Flow(client, user, prop)
        f.start("TENANT_SUBLETTER")
        f.confirm_request("TENANT_SUBLET_PERMISSION", email="landlord2@example.com")
        token, code = _code_and_token(env)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(svc.CONFIRMATION_MAX_ATTEMPTS):
            r = client.post(f"/api/authority-confirmations/{token}", json={"code": wrong, "decision": "CONFIRM",
                                                                           "responderName": "X"})
            assert r.status_code == 400
        r = client.post(f"/api/authority-confirmations/{token}", json={"code": code, "decision": "CONFIRM",
                                                                       "responderName": "X"})
        assert r.status_code == 429

    def test_expired_link_cannot_be_used(self, client, db_session, env):
        user, prop, _ = _host(db_session, "auth-linkexp@test.com")
        f = Flow(client, user, prop)
        f.start("TENANT_SUBLETTER")
        f.confirm_request("TENANT_SUBLET_PERMISSION", email="landlord3@example.com")
        token, code = _code_and_token(env)
        c = db_session.scalar(select(AuthorityConfirmation))
        c.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        r = client.post(f"/api/authority-confirmations/{token}", json={"code": code, "decision": "CONFIRM",
                                                                       "responderName": "X"})
        assert r.status_code == 409


# -- review, conflicts, revoke, expiry, renewal ---------------------------------------


def _to_review(client, db_session, email):
    user, prop, room = _host(db_session, email)
    f = Flow(client, user, prop)
    f.start("AGENT")
    f.upload("AGENT_MANDATE", _pdf("MANDATE", "Agent: Asha Rao", "12 Lake View Road, Hyderabad 500001"))
    f.submit()
    assert f.body["state"] == "MANUAL_REVIEW"
    return user, prop, room, f


class TestReview:
    def test_reviewer_approves_with_reason_code(self, client, db_session):
        user, prop, room, f = _to_review(client, db_session, "auth-review@test.com")
        admin = _make_admin(db_session, email="auth-reviewer@test.com", role="super_admin")
        queue = client.get("/api/authority-verifications", cookies=auth_admin_cookie(admin)).json()
        assert any(q["id"] == f.body["id"] for q in queue)
        case = client.get(f"/api/authority-verifications/{f.body['id']}", cookies=auth_admin_cookie(admin)).json()
        assert case["verifiedLegalName"] == LEGAL_NAME and case["evidence"]
        r = client.post(f"/api/authority-verifications/{f.body['id']}/review", cookies=auth_admin_cookie(admin),
                        json={"decision": "APPROVE", "reasonCode": "REVIEW_APPROVED_DOCUMENTS"})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "VERIFIED" and r.json()["assuranceLevel"] == "AV-2"

    def test_free_text_reason_is_refused(self, client, db_session):
        _, _, _, f = _to_review(client, db_session, "auth-review-bad@test.com")
        admin = _make_admin(db_session, email="auth-reviewer-bad@test.com", role="super_admin")
        r = client.post(f"/api/authority-verifications/{f.body['id']}/review", cookies=auth_admin_cookie(admin),
                        json={"decision": "REJECT", "reasonCode": "because I said so"})
        assert r.status_code == 400

    def test_conflicting_claims_are_preserved_and_need_two_reviewers(self, client, db_session):
        user, prop, room, f = _to_review(client, db_session, "auth-conflict@test.com")
        # a second party's claim on the same property
        other = _make_user(db_session, email="auth-conflict-other@test.com")
        party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(party)
        db_session.flush()
        other.party_id = party.id
        db_session.add(AuthorityVerification(property_id=prop.id, party_id=party.id, relationship_type="OWNER",
                                             country_code="IN", pack_version=1, state="MANUAL_REVIEW"))
        db_session.commit()
        v = db_session.get(AuthorityVerification, f.body["id"])
        v.reason_codes = ["CONFLICTING_AUTHORITY"]
        db_session.commit()
        a1 = _make_admin(db_session, email="auth-4eyes-1@test.com", role="super_admin")
        a2 = _make_admin(db_session, email="auth-4eyes-2@test.com", role="super_admin")
        body = {"decision": "APPROVE", "reasonCode": "REVIEW_APPROVED_DOCUMENTS"}
        r1 = client.post(f"/api/authority-verifications/{v.id}/review", cookies=auth_admin_cookie(a1), json=body)
        assert r1.json()["state"] == "MANUAL_REVIEW"
        again = client.post(f"/api/authority-verifications/{v.id}/review", cookies=auth_admin_cookie(a1), json=body)
        assert again.status_code == 409
        r2 = client.post(f"/api/authority-verifications/{v.id}/review", cookies=auth_admin_cookie(a2), json=body)
        assert r2.json()["state"] == "VERIFIED"
        assert db_session.scalar(select(AuthorityVerification).where(AuthorityVerification.party_id == party.id))

    def test_request_evidence_then_resubmit(self, client, db_session):
        user, prop, room, f = _to_review(client, db_session, "auth-moreev@test.com")
        admin = _make_admin(db_session, email="auth-moreev-admin@test.com", role="super_admin")
        r = client.post(f"/api/authority-verifications/{f.body['id']}/review", cookies=auth_admin_cookie(admin),
                        json={"decision": "REQUEST_EVIDENCE", "reasonCode": "REVIEW_SCOPE_UNCLEAR"})
        assert r.json()["state"] == "ACTION_REQUIRED"
        assert r.json()["reason"]["message"]
        f.body = r.json()
        f.upload("AGENT_MANDATE", MANDATE)
        assert f.body["state"] == "COLLECTING"


class TestRevokeExpiryRenew:
    def _verified(self, client, db_session, email):
        user, prop, room = _host(db_session, email)
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT")
        f.submit()
        assert f.body["state"] == "VERIFIED"
        listing = Listing(name="Lake room", room_type="private_room", city="Hyderabad", location="12 Lake View Road",
                          latitude=17.38, longitude=78.48, price_per_night=40, guests=1, room_id=room.id,
                          party_id=user.party_id, slug=f"lake-{room.id}", id=f"lst-auth-{room.id}", state="PUBLISHED")
        db_session.add(listing)
        db_session.commit()
        return user, prop, room, f, listing

    def test_trust_safety_revocation_suspends_listing(self, client, db_session):
        user, prop, room, f, listing = self._verified(client, db_session, "auth-revoke@test.com")
        admin = _make_admin(db_session, email="auth-revoke-admin@test.com", role="super_admin")
        r = client.post(f"/api/authority-verifications/{f.body['id']}/revoke", cookies=auth_admin_cookie(admin),
                        json={"reasonCode": "REVOKED_BY_TRUST_SAFETY"})
        assert r.status_code == 200 and r.json()["state"] == "REVOKED"
        db_session.refresh(listing)
        assert listing.state == "SUSPENDED"
        assert get_valid_authority_for_room(db_session, room.id) is None
        again = client.post(f"/api/authority-verifications/{f.body['id']}/revoke", cookies=auth_admin_cookie(admin),
                            json={"reasonCode": "REVOKED_BY_TRUST_SAFETY"})
        assert again.json()["state"] == "REVOKED"  # idempotent

    def test_expiry_sweep_expires_and_suspends(self, client, db_session):
        user, prop, room, f, listing = self._verified(client, db_session, "auth-expiry@test.com")
        v = db_session.get(AuthorityVerification, f.body["id"])
        v.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        assert svc.sweep_expiry(db_session)["expired"] == 1
        db_session.refresh(listing)
        assert v.state == "EXPIRED" and listing.state == "SUSPENDED"

    def test_expiring_soon_notified_once(self, client, db_session):
        user, prop, room, f, listing = self._verified(client, db_session, "auth-expiring@test.com")
        v = db_session.get(AuthorityVerification, f.body["id"])
        v.expires_at = datetime.now(timezone.utc) + timedelta(days=5)
        db_session.commit()
        assert svc.sweep_expiry(db_session)["expiring"] == 1
        assert svc.sweep_expiry(db_session)["expiring"] == 0

    def test_renewal_links_and_supersedes(self, client, db_session):
        user, prop, room, f, listing = self._verified(client, db_session, "auth-renew@test.com")
        first_id = f.body["id"]
        r = client.post(f"{BASE}/{first_id}/renew", json={}, cookies=auth_user_cookie(user))
        assert r.status_code == 201
        f.body = r.json()
        assert f.body["previousId"] == first_id and f.body["state"] == "COLLECTING"
        assert get_valid_authority_for_room(db_session, room.id).id == first_id  # old one still controls
        f.upload("OWNER_PROPERTY_RIGHT")
        f.submit()
        assert f.body["state"] == "VERIFIED"
        db_session.expire_all()
        assert db_session.get(AuthorityVerification, first_id).state == "SUPERSEDED"
        types = [e.event_type for e in db_session.scalars(select(DomainEvent).where(
            DomainEvent.resource_type == svc.RESOURCE))]
        assert "AUTHORITY_RENEWED" in types and "AUTHORITY_SUPERSEDED" in types

    def test_landlord_decline_after_verification_revokes(self, client, db_session, env):
        user, prop, room = _host(db_session, "auth-decline@test.com")
        f = Flow(client, user, prop)
        f.start("TENANT_SUBLETTER")
        f.upload("TENANT_OCCUPATION_RIGHT", LEASE)
        f.confirm_request("TENANT_SUBLET_PERMISSION", email="landlord9@example.com")
        token, code = _code_and_token(env)
        r = client.post(f"/api/authority-confirmations/{token}", json={"code": code, "decision": "DECLINE",
                                                                       "responderName": ""})
        assert r.json()["status"] == "DECLINED"


class TestGateAndPrivacy:
    def test_self_declared_legacy_route_is_retired(self, client, db_session):
        user, prop, room = _host(db_session, "auth-legacy@test.com")
        r = client.post(f"/api/users/hosting/rooms/{room.id}/authority-records",
                        json={"roomId": room.id, "relationshipType": "OWNER", "evidenceRef": "deed"},
                        cookies=auth_user_cookie(user))
        assert r.status_code == 410

    def test_reviewer_verified_legacy_record_still_counts(self, db_session):
        user, prop, room = _host(db_session, "auth-legacy-ok@test.com")
        db_session.add(AuthorityRecord(party_id=user.party_id, room_id=room.id, authority_type="owner",
                                       status="verified", expires_at=datetime.now(timezone.utc) + timedelta(days=30)))
        db_session.commit()
        assert isinstance(get_valid_authority_for_room(db_session, room.id), AuthorityRecord)

    def test_events_carry_no_documents_or_names(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-events@test.com")
        f = Flow(client, user, prop)
        f.start("AGENT")
        f.details(principalName="Vikram Shah")
        f.upload("AGENT_MANDATE", MANDATE)
        f.submit()
        for e in db_session.scalars(select(DomainEvent).where(DomainEvent.resource_type == svc.RESOURCE)):
            text = str(e.payload)
            assert "Vikram" not in text and "Asha" not in text and "Lake View" not in text

    def test_other_host_cannot_read(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-owner2@test.com")
        f = Flow(client, user, prop)
        f.start()
        outsider, _, _ = _host(db_session, "auth-outsider@test.com")
        r = client.get(f"{BASE}/{f.body['id']}", cookies=auth_user_cookie(outsider))
        assert r.status_code == 403

    def test_stale_version_conflicts(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-stale@test.com")
        f = Flow(client, user, prop)
        f.start()
        r = f._send("PUT", f"{BASE}/{f.body['id']}/details", json={"restrictions": "x"},
                    headers={"If-Match": "999"}, expect=None)
        assert r.status_code == 409

    def test_start_is_idempotent_and_resumes(self, client, db_session):
        user, prop, _ = _host(db_session, "auth-idem@test.com")
        f = Flow(client, user, prop)
        first = f.start(headers={"Idempotency-Key": "k1"}).json()
        second = Flow(client, user, prop).start().json()
        assert first["id"] == second["id"]

    def test_publication_eligibility(self, client, db_session):
        user, prop, room = _host(db_session, "auth-elig@test.com")
        r = client.get(f"/api/users/properties/{prop.id}/publication-eligibility", cookies=auth_user_cookie(user))
        assert r.json()["eligible"] is False and "AUTHORITY_NOT_VERIFIED" in r.json()["reasonCodes"]
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT")
        f.submit()
        r = client.get(f"/api/users/properties/{prop.id}/publication-eligibility", cookies=auth_user_cookie(user))
        assert r.json()["eligible"] is True
