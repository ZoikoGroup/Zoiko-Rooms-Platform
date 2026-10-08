"""ZR-AI-SEARCH-001 Section 9 steps 3-8 and Section 11, end to end:
renter request -> email with signed links -> provider response page ->
controlled introduction -> bilateral contact release -> Claim & List ->
internalisation, plus opt-outs, payment safety (SRCH-12), reports,
takedowns and Legal Pack administration.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.models.external_search import (
    ExternalMarketLegalPack,
    ExternalOpportunity,
    ExternalOpportunityReport,
    ProviderOutreach,
    ProviderSuppression,
    SourceRightRegistry,
)
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.party import Party
from app.services import provider_journey as journey
from app.services.external_outreach import outreach_service
from app.services.external_search_crypto import encrypt_optional
from app.services.outreach_worker import outreach_worker
from app.services.source_rights_registry import registry
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

pytestmark = pytest.mark.usefixtures("external_activated")

PORTAL = "/api/public/provider"
USERS = "/api/users/external-search"
ADMIN = "/api/admin/external-search"


def _rule():
    return {"s1": {"source_id": "s1", "tier": "B", "allow_fallback": True, "allow_direct_contact": True,
                   "masking_permitted": True, "is_active": True, "outreach_channels": ["EMAIL"],
                   "acquisition_mode": "LICENSED_API"}}


@pytest.fixture(autouse=True)
def _reset():
    registry._cache = _rule()
    yield
    registry._cache = None


def _opportunity(db, *, contact="owner@agency.example"):
    opp = ExternalOpportunity(
        market_code="GB", external_opportunity_id="ext_journey1", source_id="s1",
        status="EXTERNAL_DISCOVERED", approx_location="Leeds", room_type="Studio",
        provider_contact_encrypted=encrypt_optional(contact),
        advertised_price_minor=95000, advertised_price_currency="GBP", price_period="MONTH",
    )
    db.add(opp)
    db.flush()
    return opp


def _request(client, db, user, opp):
    res = client.post(
        f"{USERS}/opportunities/{opp.id}/contact",
        json={"message": "Hi, is it free from November?", "consent_fields": ["desired_area", "move_in_window", "budget_band"],
              "lead_details": {"move_in_window": "from 1 November", "budget_band": "GBP 900-1000", "occupants": "2"}},
        cookies=auth_user_cookie(user),
    )
    assert res.status_code == 200, res.text
    return db.get(ProviderOutreach, res.json()["outreach_id"])


def _sent_email(db, po):
    seen = {}

    def dispatch(_db, _po, body):
        seen["body"] = body
        return "msg-1"

    outreach_service.send_outreach(db, po, dispatch=dispatch, now=datetime.now(timezone.utc))
    return seen["body"]


def test_full_journey(client, db_session):
    renter = _make_user(db_session)
    opp = _opportunity(db_session)
    po = _request(client, db_session, renter, opp)
    assert po.consent_record["lead_details"] == {"move_in_window": "from 1 November", "budget_band": "GBP 900-1000"}

    # Step 3: the email carries only consented demand and signed links.
    body = _sent_email(db_session, po)
    assert po.outreach_status == "SENT"
    assert "from 1 November" in body and "GBP 900-1000" in body and "Occupants" not in body
    token = journey.provider_links(po.id)["token"]
    assert token in body

    # The provider's page shows the consented demand, never the renter.
    view = client.post(f"{PORTAL}/request", json={"token": token}).json()
    assert view["status"] == "AWAITING_RESPONSE" and view["can_message"] is False
    assert renter.email not in str(view)

    # Step 5: acceptance needs the terms and records model, version and ownership.
    assert client.post(f"{PORTAL}/respond", json={"token": token, "decision": "ACCEPT", "model": "CLAIM_AND_LIST"}).status_code == 422
    res = client.post(f"{PORTAL}/respond", json={"token": token, "decision": "ACCEPT", "model": "CLAIM_AND_LIST",
                                                 "provider_name": "Leeds Lets", "accepted_terms": True})
    assert res.status_code == 200, res.text
    db_session.refresh(po)
    assert po.acceptance_model == "CLAIM_AND_LIST" and po.acceptance_terms_version == "1"
    assert po.provider_contact_confirmed_at is not None
    db_session.refresh(opp)
    assert opp.verification_status == "NOT_VERIFIED_BY_ZOIKO_ROOMS"  # acceptance is not verification
    assert db_session.query(Notification).filter_by(recipient_user_id=renter.id).count() >= 1

    # Step 6: Zoiko-mediated thread, contact details scrubbed both ways.
    client.post(f"{PORTAL}/messages", json={"token": token, "body": "Yes! Call 07700 900123"})
    mine = client.post(f"{USERS}/requests/{po.id}/messages", json={"body": "Great, email me at me@renter.example"},
                       cookies=auth_user_cookie(renter)).json()
    texts = " ".join(m["body"] for m in mine["messages"])
    assert "07700" not in texts and "me@renter.example" not in texts

    # Section 11: details are released only when both sides agree.
    first = client.post(f"{USERS}/requests/{po.id}/release", cookies=auth_user_cookie(renter)).json()
    assert first["release"]["released"] is False and first["release"]["contact"] is None
    provider_side = client.post(f"{PORTAL}/release", json={"token": token}).json()
    assert provider_side["release"]["released"] is True and provider_side["release"]["contact"] == renter.email
    mine = client.get(f"{USERS}/requests", cookies=auth_user_cookie(renter)).json()[0]
    assert mine["release"]["contact"] == "owner@agency.example"


def test_claim_and_list_then_internalise_and_payment_guard(client, db_session):
    renter = _make_user(db_session)
    opp = _opportunity(db_session)
    po = _request(client, db_session, renter, opp)
    token = journey.provider_links(po.id)["token"]
    client.post(f"{PORTAL}/respond", json={"token": token, "decision": "ACCEPT", "model": "CLAIM_AND_LIST",
                                          "accepted_terms": True})

    party = Party(party_type="individual", status="active", jurisdiction="England")
    db_session.add(party)
    db_session.flush()
    host = _make_user(db_session, email="host@test.com")
    host.party_id = party.id
    db_session.flush()

    res = client.post(f"{USERS}/claim", json={"token": token}, cookies=auth_user_cookie(host))
    assert res.status_code == 200, res.text
    listing = db_session.get(Listing, res.json()["listing_id"])
    assert listing.state == "DRAFT" and listing.party_id == party.id and listing.rating == 0.0
    db_session.refresh(opp)
    assert opp.status == "VERIFICATION_IN_PROGRESS"

    # SRCH-12: no payment instructions until fully verified incl. payment authority.
    from app.crud.rental_payment import submit_rental_payment_instruction

    with pytest.raises(HTTPException) as exc:
        submit_rental_payment_instruction(db_session, party, method="BANK_TRANSFER", recipient_name="Host",
                                          country_code="GB", bank_details={}, authorized_recipient_confirmed=True)
    assert exc.value.status_code == 403

    # Step 8: publication through the normal gates internalises the lead.
    listing.state = "PUBLISHED"
    db_session.flush()
    assert journey.sync_internalised(db_session) == 1
    db_session.refresh(opp)
    assert opp.status == "INTERNALIZED_VERIFIED"
    # Still blocked until the separate payment-receipt authority is recorded.
    assert journey.payment_block_reason(db_session, party.id)
    outreach_service.record_payment_receipt_authority(db_session, opp)
    assert journey.payment_block_reason(db_session, party.id) is None


def test_opt_out_suppresses_future_outreach(client, db_session):
    renter = _make_user(db_session)
    opp = _opportunity(db_session)
    po = _request(client, db_session, renter, opp)
    token = journey.provider_links(po.id)["token"]
    assert client.post(f"{PORTAL}/opt-out", json={"token": token}).json() == {"opted_out": True}
    assert db_session.query(ProviderSuppression).count() == 1
    db_session.refresh(po)
    assert po.provider_response == "DECLINED"

    # A new request for the same provider is refused (SRCH-09).
    other = _make_user(db_session, email="other@test.com")
    res = client.post(f"{USERS}/opportunities/{opp.id}/contact",
                      json={"message": "Hello", "consent_fields": ["desired_area"]}, cookies=auth_user_cookie(other))
    assert res.status_code == 403 and "opted out" in res.text


def test_pending_outreach_is_suppressed_by_worker_after_opt_out(db_session):
    renter = _make_user(db_session)
    opp = _opportunity(db_session)
    po = outreach_service.request_intro(db_session, user_id=renter.id, opportunity_id=opp.id)
    journey.suppress(db_session, contact="OWNER@agency.example", created_by="admin:1")
    outreach_worker.process_pending_outreach(db_session, dispatch=lambda *_: "msg")
    assert po.outreach_status == "SUPPRESSED"


def test_market_without_outreach_or_channel_fails_closed(db_session):
    renter = _make_user(db_session)
    opp = _opportunity(db_session)
    pack = db_session.query(ExternalMarketLegalPack).filter_by(market_code="GB").one()
    pack.provider_outreach_enabled = False
    db_session.flush()
    with pytest.raises(PermissionError, match="not activated for this market"):
        outreach_service.request_intro(db_session, user_id=renter.id, opportunity_id=opp.id)
    pack.provider_outreach_enabled, pack.permitted_outreach_channels = True, ["SMS"]
    db_session.flush()
    with pytest.raises(PermissionError, match="no permitted channel"):
        outreach_service.request_intro(db_session, user_id=renter.id, opportunity_id=opp.id)


def test_release_needs_market_permission(client, db_session):
    renter = _make_user(db_session)
    po = _request(client, db_session, renter, _opportunity(db_session))
    token = journey.provider_links(po.id)["token"]
    client.post(f"{PORTAL}/respond", json={"token": token, "decision": "ACCEPT", "model": "CLAIM_AND_LIST", "accepted_terms": True})
    pack = db_session.query(ExternalMarketLegalPack).filter_by(market_code="GB").one()
    pack.direct_contact_release_enabled = False
    db_session.flush()
    assert client.post(f"{USERS}/requests/{po.id}/release", cookies=auth_user_cookie(renter)).status_code == 409


def test_forged_or_expired_links_are_refused(client, db_session, monkeypatch):
    assert client.post(f"{PORTAL}/request", json={"token": "abc.def0123456789"}).status_code == 403
    renter = _make_user(db_session)
    po = _request(client, db_session, renter, _opportunity(db_session))
    expired = journey.make_provider_token(po.id, ttl_days=-1)
    assert client.post(f"{PORTAL}/request", json={"token": expired}).status_code == 403


def test_one_off_introduction_only_where_enabled(client, db_session):
    renter = _make_user(db_session)
    po = _request(client, db_session, renter, _opportunity(db_session))
    token = journey.provider_links(po.id)["token"]
    assert client.post(f"{PORTAL}/request", json={"token": token}).json()["options"] == ["CLAIM_AND_LIST"]
    res = client.post(f"{PORTAL}/respond", json={"token": token, "decision": "ACCEPT",
                                                 "model": "ONE_OFF_INTRODUCTION", "accepted_terms": True})
    assert res.status_code == 422


def test_renter_report_and_admin_takedown(client, db_session):
    renter = _make_user(db_session)
    opp = _opportunity(db_session)
    res = client.post(f"{USERS}/opportunities/{opp.id}/report", json={"reason": "STALE", "note": "Already let"},
                      cookies=auth_user_cookie(renter))
    assert res.status_code == 200
    assert db_session.query(ExternalOpportunityReport).count() == 1

    db_session.add(SourceRightRegistry(source_id="s1", source_name_internal="s1", territories=["GB"],
                                       acquisition_mode="LICENSED_API", status="ACTIVE"))
    db_session.flush()
    admin = _make_admin(db_session, role="super_admin")
    res = client.post(f"{ADMIN}/registry/s1/takedown", json={"note": "Source complaint"}, cookies=auth_admin_cookie(admin))
    assert res.status_code == 200 and res.json()["withdrawn_leads"] == 1
    db_session.refresh(opp)
    assert opp.status == "BLOCKED"


def test_market_pack_admin_versions_and_rules(client, db_session):
    admin = _make_admin(db_session, role="super_admin")
    base = {"status": "ACTIVE", "legal_approved": True, "privacy_approved": True, "commercial_approved": True,
            "external_search_enabled": True}
    res = client.put(f"{ADMIN}/market-packs/US", json=base, cookies=auth_admin_cookie(admin))
    assert res.status_code == 200 and res.json()["version"] == 2  # fixture created version 1
    assert client.put(f"{ADMIN}/market-packs/US", json={**base, "privacy_approved": False},
                      cookies=auth_admin_cookie(admin)).status_code == 422
    assert client.put(f"{ADMIN}/market-packs/US", json={**base, "referral_fees_enabled": True},
                      cookies=auth_admin_cookie(admin)).status_code == 422
    assert client.put(f"{ADMIN}/market-packs/ZZ", json=base, cookies=auth_admin_cookie(admin)).status_code == 422


def test_sublet_permission_needs_evidence(db_session):
    renter = _make_user(db_session)
    opp = _opportunity(db_session)
    opp.status = "PROVIDER_ACCEPTED"
    db_session.flush()
    with pytest.raises(ValueError):
        outreach_service.record_sublet_permission(db_session, opp, evidence=None)
    outreach_service.record_sublet_permission(db_session, opp, evidence="Landlord letter dated 1 Oct")
    assert opp.sublet_permission_verified is True
    assert renter is not None


def test_host_reconfirms_availability_and_gets_reminders(client, db_session):
    from app.services.scheduled_jobs import remind_hosts_to_reconfirm_availability
    from tests.test_search_orchestrator import _seed_listing

    party = Party(party_type="individual", status="active", jurisdiction="England")
    db_session.add(party)
    db_session.flush()
    host = _make_user(db_session, email="host2@test.com")
    host.party_id = party.id
    listing = _seed_listing(db_session, slug="fresh-1", city="Leeds")
    listing.party_id = party.id
    db_session.flush()
    monkey = db_session.commit
    db_session.commit = db_session.flush
    try:
        assert remind_hosts_to_reconfirm_availability(db_session) == 1
        assert remind_hosts_to_reconfirm_availability(db_session) == 0   # once per period
    finally:
        db_session.commit = monkey
    res = client.post(f"{USERS}/listings/{listing.id}/confirm-availability", cookies=auth_user_cookie(host))
    assert res.status_code == 200 and res.json()["availability_confirmed_at"]
    other = _make_user(db_session, email="stranger@test.com")
    assert client.post(f"{USERS}/listings/{listing.id}/confirm-availability",
                       cookies=auth_user_cookie(other)).status_code == 404


def test_appendix_a_config_endpoint(client, db_session):
    admin = _make_admin(db_session, role="super_admin")
    cfg = client.get(f"{ADMIN}/protocol-config", cookies=auth_admin_cookie(admin)).json()
    assert cfg["external_required_cta"]["value"] == "REQUEST_ZOIKO_CONTACT"
    assert cfg["unknown_source_rights"]["value"] == "FAIL_CLOSED"
    # Reports the live setting (the suite's legacy-payment fixture may turn it on).
    from app.core.config import settings

    assert cfg["rent_collection_enabled"]["value"] is settings.rent_collection_enabled
