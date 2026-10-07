"""ZR-AUTHORITY-002 gaps closed after the first build: the server-side
publication gate (Section 2.4 / P0 #10 / 15.3), ownership-change and
identity-event triggers (Sections 9.2, 13.1), the pack's renewal threshold,
trusted registry / connector sources (Sections 4.1, 5.2, 6.3, 7.3), AV-2
assurance (Section 2.3), status emails (Section 15.3) and audited evidence
removal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.models.authority_verification import AuthorityEvidence, AuthorityVerification
from app.models.domain_event import DomainEvent
from app.models.listing import Listing
from app.models.party import Party
from app.services import authority_service as svc
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie, make_room_publishable
from tests.test_authority_verification import (  # noqa: F401 -- env is an autouse fixture
    BASE, LEASE, LEGAL_NAME, MANDATE, Flow, _code_and_token, _host, _pdf, env,
)


def _verified_with_listing(client, db_session, email, state="PUBLISHED"):
    user, prop, room = _host(db_session, email)
    f = Flow(client, user, prop)
    f.start()
    f.upload("OWNER_PROPERTY_RIGHT")
    f.submit()
    assert f.body["state"] == "VERIFIED"
    make_room_publishable(db_session, room)
    listing = Listing(name="Lake room", room_type="private_room", city="Hyderabad", location="12 Lake View Road",
                      latitude=17.38, longitude=78.48, price_per_night=40, guests=1, room_id=room.id,
                      party_id=user.party_id, slug=f"lake-{room.id}", id=f"lst-gate-{room.id}", state=state)
    db_session.add(listing)
    db_session.commit()
    return user, prop, room, f, listing


def _event_types(db_session, resource_id=None):
    query = select(DomainEvent.event_type).where(DomainEvent.resource_type == svc.RESOURCE)
    if resource_id is not None:
        query = query.where(DomainEvent.resource_id == str(resource_id))
    return list(db_session.scalars(query))


class TestPublicationGate:
    def test_revocation_blocks_republish_until_renewed(self, client, db_session):
        user, prop, room, f, listing = _verified_with_listing(client, db_session, "gate-revoke@test.com")
        admin = _make_admin(db_session, email="gate-admin@test.com", role="super_admin")
        cookies = auth_admin_cookie(admin)
        client.post(f"/api/authority-verifications/{f.body['id']}/revoke", cookies=cookies,
                    json={"reasonCode": "REVOKED_BY_TRUST_SAFETY"})
        db_session.refresh(listing)
        assert listing.state == "SUSPENDED"
        r = client.post(f"/api/listings/{listing.id}/publish", cookies=cookies)
        assert r.status_code == 409 and "authority" in r.json()["detail"].lower()
        f.body = client.post(f"{BASE}/{f.body['id']}/renew", json={}, cookies=auth_user_cookie(user)).json()
        f.upload("OWNER_PROPERTY_RIGHT")
        f.submit()
        assert f.body["state"] == "VERIFIED"
        r = client.post(f"/api/listings/{listing.id}/publish", cookies=cookies)
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "PUBLISHED"

    def test_resume_rechecks_expired_authority(self, client, db_session):
        user, prop, room, f, listing = _verified_with_listing(client, db_session, "gate-resume@test.com",
                                                              state="PAUSED")
        v = db_session.get(AuthorityVerification, f.body["id"])
        v.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        admin = _make_admin(db_session, email="gate-resume-admin@test.com", role="super_admin")
        r = client.post(f"/api/listings/{listing.id}/resume", cookies=auth_admin_cookie(admin))
        assert r.status_code == 409
        db_session.refresh(listing)
        assert listing.state == "PAUSED"


class TestTriggers:
    def test_ownership_change_reopens_owner_and_agent_only(self, client, db_session):
        user, prop, room, f, listing = _verified_with_listing(client, db_session, "own-change@test.com")
        tenant_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(tenant_party)
        db_session.flush()
        tenant = AuthorityVerification(property_id=prop.id, party_id=tenant_party.id,
                                       relationship_type="TENANT_SUBLETTER", country_code="IN", state="MANUAL_REVIEW")
        db_session.add(tenant)
        db_session.commit()
        admin = _make_admin(db_session, email="own-change-admin@test.com", role="super_admin")
        r = client.post(f"/api/authority-verifications/properties/{prop.id}/ownership-change",
                        cookies=auth_admin_cookie(admin))
        assert r.status_code == 200 and r.json()["reopened"] == 1
        db_session.expire_all()
        owner = db_session.get(AuthorityVerification, f.body["id"])
        assert owner.state == "REVOKED" and owner.revocation_reason_code == "OWNERSHIP_CHANGED"
        assert db_session.get(AuthorityVerification, tenant.id).state == "MANUAL_REVIEW"
        assert db_session.get(Listing, listing.id).state == "SUSPENDED"

    def test_identity_events_reopen_authority_but_periodic_renewal_does_not(self, client, db_session):
        user, prop, room, f, listing = _verified_with_listing(client, db_session, "id-event@test.com")
        assert svc.reopen_for_identity_event(db_session, user.party_id, "PERIODIC_RENEWAL") == 0
        assert svc.reopen_for_identity_event(db_session, user.party_id, "ACCOUNT_RECOVERY") == 1
        db_session.commit()
        v = db_session.get(AuthorityVerification, f.body["id"])
        assert v.state == "REVOKED" and v.revocation_reason_code == "IDENTITY_REVERIFICATION"
        assert db_session.get(Listing, listing.id).state == "SUSPENDED"

    def test_identity_reverification_hook_reaches_authority(self, db_session, monkeypatch):
        from app.services.identity import service as identity_service

        calls = []
        monkeypatch.setattr(svc, "reopen_for_identity_event",
                            lambda db, party_id, reason, **kw: calls.append((party_id, reason)) or 0)
        monkeypatch.setattr(identity_service, "_event", lambda *a, **kw: None)
        profile = SimpleNamespace(state="VERIFIED", party_id=42, current_verification_id=None)
        identity_service.mark_reverification_required(db_session, profile, "LEGAL_DETAILS_CHANGED")
        assert calls == [(42, "LEGAL_DETAILS_CHANGED")]

    def test_expiring_soon_uses_pack_threshold(self, client, db_session):
        user, prop, room, f, listing = _verified_with_listing(client, db_session, "pack-days@test.com")
        svc.get_pack(db_session, "IN").expiring_soon_days = 10
        v = db_session.get(AuthorityVerification, f.body["id"])
        v.expires_at = datetime.now(timezone.utc) + timedelta(days=20)
        db_session.commit()
        assert svc.effective_state(v) == "VERIFIED"
        v.expires_at = datetime.now(timezone.utc) + timedelta(days=5)
        db_session.commit()
        assert svc.effective_state(v) == "EXPIRING_SOON"


class _FakeRegistry(svc.SourceAdapter):
    code = "test-registry"
    source_type = "REGISTRY"
    countries = ("IN",)
    evidence_classes = ("PROPERTY_RIGHT",)
    label = "Test land registry"

    def lookup(self, prop, reference, evidence_class):
        if reference != "TITLE-1":
            return {"found": False}
        return {"found": True, "issuer": "Test land registry", "reference": reference,
                "property_matched": True, "holder_name": LEGAL_NAME}


class TestTrustedSources:
    def test_registry_check_verifies_owner_without_upload(self, client, db_session, monkeypatch):
        monkeypatch.setitem(svc.SOURCE_ADAPTERS, "test-registry", _FakeRegistry())
        user, prop, room = _host(db_session, "source-owner@test.com")
        f = Flow(client, user, prop)
        f.start()
        req = next(r for r in f.body["requirements"] if r["requirement_id"] == "OWNER_PROPERTY_RIGHT")
        assert req["sources"] == [{"code": "test-registry", "source_type": "REGISTRY", "label": "Test land registry"}]
        url = f"{BASE}/{f.body['id']}/source-checks"
        payload = {"requirementId": "OWNER_PROPERTY_RIGHT", "sourceCode": "test-registry"}
        assert f._send("POST", url, json={**payload, "reference": "NOPE"}, expect=None).status_code == 404
        f._send("POST", url, json={**payload, "reference": "TITLE-1"})
        assert f.body["evidence"][0]["sourceType"] == "REGISTRY"
        f.submit()
        assert f.body["state"] == "VERIFIED"

    def test_no_sources_offered_without_an_adapter(self, client, db_session):
        user, prop, room = _host(db_session, "source-none@test.com")
        f = Flow(client, user, prop)
        f.start()
        assert all(r["sources"] == [] for r in f.body["requirements"])
        r = f._send("POST", f"{BASE}/{f.body['id']}/source-checks", expect=None,
                    json={"requirementId": "OWNER_PROPERTY_RIGHT", "sourceCode": "x", "reference": "y"})
        assert r.status_code == 400


class TestAssuranceEmailsAudit:
    def test_document_plus_principal_confirmation_is_av2(self, client, db_session, env):
        user, prop, room = _host(db_session, "av2-agent@test.com")
        f = Flow(client, user, prop)
        f.start("AGENT")
        f.details(principalName="Vikram Shah")
        f.upload("AGENT_MANDATE", MANDATE)
        f.confirm_request("AGENT_MANDATE", email="vikram@example.com")
        token, code = _code_and_token(env)
        r = client.post(f"/api/authority-confirmations/{token}",
                        json={"code": code, "decision": "CONFIRM", "responderName": "Vikram Shah"})
        assert r.json()["status"] == "CONFIRMED"
        f._send("GET", f"{BASE}/{f.body['id']}")
        f.submit()
        assert f.body["state"] == "VERIFIED" and f.body["assuranceLevel"] == "AV-2"

    def test_status_emails_are_sent(self, client, db_session, monkeypatch):
        import app.core.mailer as mailer

        sent = []
        monkeypatch.setattr(mailer, "send_authority_status_email",
                            lambda to, name, **kw: sent.append(kw["variant"]))
        _verified_with_listing(client, db_session, "emails@test.com")
        assert "approved" in sent

    def test_verification_center_lists_current_assertion_per_property(self, client, db_session):
        user, prop, room, f, listing = _verified_with_listing(client, db_session, "center@test.com")
        client.post(f"{BASE}/{f.body['id']}/renew", json={}, cookies=auth_user_cookie(user))
        rows = client.get(BASE, cookies=auth_user_cookie(user)).json()
        assert len(rows) == 1 and rows[0]["propertyId"] == prop.id
        assert rows[0]["state"] == "COLLECTING"  # the renewal in progress is the current assertion
        assert "evidence" not in rows[0] and "requirements" not in rows[0]

    def test_sample_document_is_not_accepted_even_when_everything_matches(self, client, db_session):
        user, prop, room = _host(db_session, "sample-doc@test.com")
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT", _pdf("SALE DEED - SAMPLE DOCUMENT", "Owner: Asha Rao",
                                              "Property: 12 Lake View Road, Hyderabad 500001",
                                              "Not valid for any legal or government submission"))
        f.submit()
        assert f.body["state"] == "ACTION_REQUIRED"
        assert "DOCUMENT_NOT_OFFICIAL" in f.body["reasonCodes"]

    def test_specimen_signature_on_a_real_power_of_attorney_is_fine(self, client, db_session):
        user, prop, room = _host(db_session, "specimen-sig@test.com")
        f = Flow(client, user, prop)
        f.start("AGENT")
        f.details(principalName="Vikram Shah")
        f.upload("AGENT_MANDATE", _pdf("GENERAL POWER OF ATTORNEY", "Executant: Vikram Shah", "Attorney: Asha Rao",
                                       "Property: 12 Lake View Road, Hyderabad 500001",
                                       "Specimen signature of the attorney"))
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]

    def test_general_noc_is_not_permission_to_sublet(self, client, db_session):
        user, prop, room = _host(db_session, "general-noc@test.com")
        f = Flow(client, user, prop)
        f.start("TENANT_SUBLETTER")
        f.details(principalName="Ravi Kumar")
        f.upload("TENANT_OCCUPATION_RIGHT", LEASE)
        f.upload("TENANT_SUBLET_PERMISSION", _pdf("NO OBJECTION CERTIFICATE", "Landlord: Ravi Kumar",
                                                  "No objection to an electricity connection for Asha Rao at",
                                                  "12 Lake View Road, Hyderabad 500001"))
        f.submit()
        assert f.body["state"] == "ACTION_REQUIRED"
        assert "DOCUMENT_TYPE_UNRECOGNIZED" in f.body["reasonCodes"]

    def test_upload_shows_which_listed_document_it_was_recognised_as(self, client, db_session):
        user, prop, room = _host(db_session, "detected-doc@test.com")
        f = Flow(client, user, prop)
        f.start()
        req = next(r for r in f.body["requirements"] if r["requirement_id"] == "OWNER_PROPERTY_RIGHT")
        assert [d["label"] for d in req["documents"]][:3] == ["Sale deed", "Gift / partition / settlement deed",
                                                             "Encumbrance certificate"]
        f.upload("OWNER_PROPERTY_RIGHT", _pdf("ENCUMBRANCE CERTIFICATE", "Owner: Asha Rao",
                                              "12 Lake View Road, Hyderabad 500001"))
        assert f.body["evidence"][0]["detectedDocument"] == "Encumbrance certificate"
        assert f.body["evidence"][0]["recognised"] is True

    def test_joined_given_names_still_match(self):
        """Indian documents often write "ANIL KUMAR" as "ANILKUMAR"."""
        match = svc._name_in_text
        assert match("PAYEE NAME: NAKULURI ANILKUMAR", "NAKULURI ANIL KUMAR")
        assert match("ANILKUMAR NAKULURI", "Nakuluri Anil Kumar")
        assert not match("SUNIL KUMAR NAKULURI", "NAKULURI ANIL KUMAR")
        assert not match("ASHA RAOBERT", "Asha Rao")

    def test_evidence_removal_is_audited(self, client, db_session):
        user, prop, room = _host(db_session, "remove-audit@test.com")
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT")
        f._send("DELETE", f"{BASE}/{f.body['id']}/evidence/{f.body['evidence'][0]['id']}")
        assert "AUTHORITY_EVIDENCE_REMOVED" in _event_types(db_session)


class TestPacksStaleUploadsRenewal:
    def _admin(self, db_session):
        return auth_admin_cookie(_make_admin(db_session, email="packs-admin@test.com", role="super_admin"))

    def test_pack_edit_saves_a_new_version_with_new_keywords(self, client, db_session):
        cookies = self._admin(db_session)
        packs = client.get("/api/authority-verifications/packs", cookies=cookies).json()
        india = next(p for p in packs if p["countryCode"] == "IN")
        reqs = india["requirements"]
        reqs["OWNER"][0]["documents"] = [{"label": "Sale deed", "keywords": ["Sale Deed", "sale deed"]},
                                          {"label": "Gift deed", "keywords": ["Registered Gift Deed"]}]
        r = client.put(f"/api/authority-verifications/packs/{india['id']}", cookies=cookies,
                       json={"requirements": reqs, "expiringSoonDays": 45})
        assert r.status_code == 200, r.text
        new = next(p for p in client.get("/api/authority-verifications/packs", cookies=cookies).json()
                   if p["countryCode"] == "IN")
        assert new["version"] == india["version"] + 1 and new["expiringSoonDays"] == 45
        owner = new["requirements"]["OWNER"][0]
        assert owner["documents"] == [{"label": "Sale deed", "keywords": ["sale deed"]},
                                      {"label": "Gift deed", "keywords": ["registered gift deed"]}]
        assert owner["accepted_examples"] == ["Sale deed", "Gift deed"]

    def test_pack_edit_cannot_drop_requirements_or_empty_keywords(self, client, db_session):
        cookies = self._admin(db_session)
        india = next(p for p in client.get("/api/authority-verifications/packs", cookies=cookies).json()
                     if p["countryCode"] == "IN")
        url = f"/api/authority-verifications/packs/{india['id']}"
        dropped = {**india["requirements"], "SUBLET": india["requirements"]["SUBLET"][:1]}
        assert client.put(url, cookies=cookies, json={"requirements": dropped}).status_code == 400
        empty = india["requirements"]
        empty["OWNER"][0]["documents"] = [{"label": "Sale deed", "keywords": []}]
        assert client.put(url, cookies=cookies, json={"requirements": empty}).status_code == 400
        assert client.put(url, cookies=cookies, json={"defaultValidityDays": 5}).status_code == 400

    def test_upload_read_before_ocr_is_read_again_on_submit(self, client, db_session):
        user, prop, room = _host(db_session, "stale-upload@test.com")
        f = Flow(client, user, prop)
        f.start()
        f.upload("OWNER_PROPERTY_RIGHT")
        e = db_session.scalar(select(AuthorityEvidence))
        e.text_source, e.readable, e.type_matched, e.property_matched, e.name_matched = "", False, None, None, None
        db_session.commit()
        f.submit()
        assert f.body["state"] == "VERIFIED", f.body["reasonCodes"]

    def test_renewal_needs_a_verified_identity(self, client, db_session, env):
        user, prop, room, f, listing = _verified_with_listing(client, db_session, "renew-id@test.com")
        env["identity"] = False
        r = client.post(f"{BASE}/{f.body['id']}/renew", json={}, cookies=auth_user_cookie(user))
        assert r.status_code == 409 and "identity" in r.json()["detail"].lower()
