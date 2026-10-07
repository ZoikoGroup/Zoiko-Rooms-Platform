"""REST endpoint tests for the ZR-AI-SEARCH-001 external search protocol.

Covers the thin /api/users/external-search/* and /api/admin/external-search/*
surface added in Phase 0, which share the deterministic orchestrator/outreach
services with the chat tools. SQLite in-memory DB via the shared fixtures.
"""

from __future__ import annotations

import pytest

from app.models.external_search import ExternalOpportunity
from app.models.listing import Listing
from app.services.source_rights_registry import registry

from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

# External discovery and outreach are off until market activation (Section 13).
pytestmark = pytest.mark.usefixtures("external_activated")

SEARCH_URL = "/api/users/external-search/search"
CONTACT_URL = "/api/users/external-search/opportunities/{id}/contact"
MY_OPP_URL = "/api/users/external-search/opportunities"
ADMIN_QUEUE_URL = "/api/admin/external-search/outreach"
ADMIN_REGISTRY_URL = "/api/admin/external-search/registry"


def _rule(source_id: str, *, fallback: bool = False, contact: bool = False) -> dict:
    return {
        "source_id": source_id,
        "domain": "example.org",
        "tier": "A" if contact else "B",
        "allow_fallback": fallback,
        "allow_direct_contact": contact,
        "allow_indexing": False,
        "policy_ref": "ZR-POL-SRCH-001",
        "notes": None,
        "is_active": True,
        "outreach_channels": ["PLATFORM_MESSAGE"] if contact else [],
    }


LEAD = ["desired_area", "move_in_window"]


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    registry._cache = None
    yield
    registry._cache = None


def _publish_listing(db, listing_id: str = "TST-1001", city: str = "Mumbai") -> Listing:
    listing = Listing(
        id=listing_id,
        slug=listing_id.lower(),
        name="Bright Private Room",
        property_type="private_room",
        room_type="private_room",
        city=city,
        location="Andheri",
        price_per_night=1000.0,
        currency="INR",
        guests=2,
        state="PUBLISHED",
        description="Sunny room near the metro",
    )
    db.add(listing)
    db.flush()
    return listing


def _seed_opportunity(db, source_id: str = "s1", *, status: str = "EXTERNAL_DISCOVERED") -> ExternalOpportunity:
    opp = ExternalOpportunity(
        external_opportunity_id=f"ext_{source_id}",
        source_id=source_id,
        status=status,
        approx_location="Bristol city centre area",
        advertised_price_currency="GBP",
        advertised_price_minor=85000,
        price_period="MONTH",
        room_type="PRIVATE_ROOM",
    )
    db.add(opp)
    db.flush()
    return opp


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_search_requires_user_auth(client, db_session):
    assert client.post(SEARCH_URL, json={"city": "Atlantis"}).status_code == 401


def test_admin_queue_requires_admin_auth(client, db_session):
    assert client.get(ADMIN_QUEUE_URL).status_code == 401


def test_admin_queue_rejects_user_cookie(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    assert client.get(ADMIN_QUEUE_URL, cookies=auth_user_cookie(user)).status_code == 401


def test_registry_requires_super_admin(client, db_session):
    from tests.conftest import _make_admin

    admin = _make_admin(db_session, role="admin")
    res = client.get(ADMIN_REGISTRY_URL, cookies=auth_admin_cookie(admin))
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Search endpoint
# ---------------------------------------------------------------------------


def test_search_external_fallback_masked(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    registry._cache = {"demo_external": _rule("demo_external", fallback=True)}

    res = client.post(SEARCH_URL, json={"city": "Atlantis"}, cookies=auth_user_cookie(user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "EXTERNAL_DISCOVERED"
    assert body["internal_matches"] == 0
    assert body["fallback_triggered"] is True
    assert body["consent_required"] is True
    assert "disclosure_text" in body and body["disclosure_text"]
    assert body["external_matches"], "expected at least one masked external card"

    for card in body["external_matches"]:
        for forbidden in ("source_url", "provider_phone", "provider_email", "exact_address", "phone", "email", "url", "address"):
            assert forbidden not in card, f"leaked field {forbidden!r} in card: {card}"
        assert card["opportunity_id"] >= 1, "expect a persisted opportunity id per discovered card"
        assert "NOT_VERIFIED_BY_ZOIKO_ROOMS" not in card or card["verification_status"] == "unverified"


def test_search_internal_verified_wins(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    _publish_listing(db_session, city="Mumbai")
    registry._cache = {"demo_external": _rule("demo_external", fallback=True)}

    res = client.post(SEARCH_URL, json={"city": "Mumbai"}, cookies=auth_user_cookie(user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "INTERNAL_VERIFIED"
    assert body["internal_matches"] == 1
    assert body["external_matches"] == []
    assert body["fallback_triggered"] is False
    assert body["internal_results"], "expected internal rows"


def test_search_blocked_when_no_eligible_external(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    registry._cache = {"demo_external": _rule("demo_external", fallback=False)}

    res = client.post(SEARCH_URL, json={"city": "Atlantis"}, cookies=auth_user_cookie(user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "BLOCKED"
    assert body["external_matches"] == []
    assert body["disclosure_text"].startswith("No matching Zoiko Rooms listings were found")


def test_search_accepts_section5_filters(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    _publish_listing(db_session, city="Mumbai")
    registry._cache = {"demo_external": _rule("demo_external", fallback=True)}

    res = client.post(
        SEARCH_URL,
        json={
            "city": "Mumbai",
            "room_type": "private_room",
            "move_in_from": "2026-09-01",
            "move_in_to": "2026-10-01",
            "objective_filters": [],
        },
        cookies=auth_user_cookie(user),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "INTERNAL_VERIFIED"
    assert body["internal_matches"] == 1
    assert isinstance(body["internal_results"][0]["roomType"], str)


def test_search_internal_rows_carry_freshness_signal(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    _publish_listing(db_session, city="Mumbai")
    res = client.post(
        SEARCH_URL, json={"city": "Mumbai"}, cookies=auth_user_cookie(user)
    )
    assert res.status_code == 200, res.text
    row = res.json()["internal_results"][0]
    assert "availabilityConfirmedAt" in row
    assert "availabilityFreshStale" in row


# ---------------------------------------------------------------------------
# Section 8 abuse controls -- per-actor rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_returns_429_after_window_exhausted(client, db_session):
    from tests.conftest import _make_user

    from app.core.config import settings
    from app.core.rate_limit import external_search_limiter

    user = _make_user(db_session)
    key = f"external_search:search:{user.id}"
    external_search_limiter.reset(key)
    try:
        for _ in range(settings.external_search_rate_limit_max):
            assert external_search_limiter.allow(key) is True
        res = client.post(
            SEARCH_URL, json={"city": "Atlantis"}, cookies=auth_user_cookie(user)
        )
        assert res.status_code == 429
        assert "rate limit" in res.json()["detail"].lower()
    finally:
        external_search_limiter.reset(key)


def test_rate_limit_is_per_actor(client, db_session):
    from tests.conftest import _make_user

    from app.core.config import settings
    from app.core.rate_limit import external_search_limiter

    user_a = _make_user(db_session, email="user-a@test.com")
    user_b = _make_user(db_session, email="user-b@test.com")
    external_search_limiter.reset(None)
    try:
        for _ in range(settings.external_search_rate_limit_max):
            external_search_limiter.allow(f"external_search:search:{user_a.id}")
        # user B is unaffected by user A's budget.
        res = client.post(
            SEARCH_URL, json={"city": "Atlantis"}, cookies=auth_user_cookie(user_b)
        )
        assert res.status_code == 200, res.text
    finally:
        external_search_limiter.reset(None)


def test_contact_route_has_own_rate_limit_bucket(client, db_session):
    from tests.conftest import _make_user

    from app.core.config import settings
    from app.core.rate_limit import external_search_limiter

    user = _make_user(db_session)
    opp = _seed_opportunity(db_session, source_id="s1")
    registry._cache = {"s1": _rule("s1", contact=True)}
    external_search_limiter.reset(None)
    try:
        for _ in range(settings.external_search_rate_limit_max):
            external_search_limiter.allow(f"external_search:contact:{user.id}")
        res = client.post(
            CONTACT_URL.format(id=opp.id),
            json={"message": "Hello", "consent_fields": LEAD},
            cookies=auth_user_cookie(user),
        )
        assert res.status_code == 429
    finally:
        external_search_limiter.reset(None)


# ---------------------------------------------------------------------------
# Provider contact
# ---------------------------------------------------------------------------


def test_request_contact_creates_outreach(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    opp = _seed_opportunity(db_session, source_id="s1")
    registry._cache = {"s1": _rule("s1", contact=True)}

    res = client.post(
        CONTACT_URL.format(id=opp.id),
        json={"message": "Please connect me", "consent_fields": LEAD},
        cookies=auth_user_cookie(user),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "PENDING"
    assert body["channel"] == "PLATFORM_MESSAGE"
    assert body["outreach_id"] >= 1

    # Visible in the user's list, masked.
    mine = client.get(MY_OPP_URL, cookies=auth_user_cookie(user))
    assert mine.status_code == 200
    rows = mine.json()
    assert len(rows) == 1
    assert rows[0]["opportunity_id"] == opp.id
    assert rows[0]["outreach_status"] == "PENDING"
    assert rows[0]["masked"] is True
    assert "provider_contact" not in rows[0]


def test_request_contact_blocked_opportunity(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    opp = _seed_opportunity(db_session, source_id="s1", status="BLOCKED")
    registry._cache = {"s1": _rule("s1", contact=True)}

    res = client.post(
        CONTACT_URL.format(id=opp.id),
        json={"message": "Hello", "consent_fields": LEAD},
        cookies=auth_user_cookie(user),
    )
    assert res.status_code == 403


def test_request_contact_rights_denied(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    opp = _seed_opportunity(db_session, source_id="s1")
    registry._cache = {"s1": _rule("s1", contact=False)}

    res = client.post(
        CONTACT_URL.format(id=opp.id),
        json={"message": "Hello", "consent_fields": LEAD},
        cookies=auth_user_cookie(user),
    )
    assert res.status_code == 403


def test_request_contact_unknown_opportunity(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    registry._cache = {"s1": _rule("s1", contact=True)}

    res = client.post(
        CONTACT_URL.format(id=999999),
        json={"message": "Hello", "consent_fields": LEAD},
        cookies=auth_user_cookie(user),
    )
    assert res.status_code == 404


def test_request_contact_records_intro_and_advances_status(client, db_session):
    from tests.conftest import _make_user

    from app.models.audit import AuditEvent

    user = _make_user(db_session)
    opp = _seed_opportunity(db_session, source_id="s1")
    registry._cache = {"s1": _rule("s1", contact=True)}

    res = client.post(
        CONTACT_URL.format(id=opp.id),
        json={"message": "Hello", "consent_fields": LEAD},
        cookies=auth_user_cookie(user),
    )
    assert res.status_code == 200, res.text
    db_session.refresh(opp)
    assert opp.status == "OUTREACH_PENDING"
    assert db_session.query(AuditEvent).filter_by(action="outreach.intro_requested").count() == 1


def test_request_contact_rejects_identity_fields_before_acceptance(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    opp = _seed_opportunity(db_session, source_id="s1")
    registry._cache = {"s1": _rule("s1", contact=True)}

    for fields in (["name"], ["desired_area", "phone"], []):
        res = client.post(
            CONTACT_URL.format(id=opp.id),
            json={"message": "Hello", "consent_fields": fields},
            cookies=auth_user_cookie(user),
        )
        assert res.status_code == 422, fields


def test_request_contact_on_another_users_opportunity_is_not_found(client, db_session):
    from tests.conftest import _make_user

    owner = _make_user(db_session)
    other = _make_user(db_session, email="other@test.com")
    opp = _seed_opportunity(db_session, source_id="s1")
    opp.discovered_by_user_id = owner.id
    db_session.flush()
    registry._cache = {"s1": _rule("s1", contact=True)}

    res = client.post(
        CONTACT_URL.format(id=opp.id),
        json={"message": "Hello", "consent_fields": LEAD},
        cookies=auth_user_cookie(other),
    )
    assert res.status_code == 404


def test_repeat_contact_request_reuses_open_outreach(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    opp = _seed_opportunity(db_session, source_id="s1")
    registry._cache = {"s1": _rule("s1", contact=True)}

    ids = set()
    for _ in range(2):
        res = client.post(
            CONTACT_URL.format(id=opp.id),
            json={"message": "Hello", "consent_fields": LEAD},
            cookies=auth_user_cookie(user),
        )
        assert res.status_code == 200, res.text
        ids.add(res.json()["outreach_id"])
    assert len(ids) == 1


def test_request_contact_without_configured_channel_is_denied(client, db_session):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    opp = _seed_opportunity(db_session, source_id="s1")
    rule = _rule("s1", contact=True)
    rule["outreach_channels"] = []
    registry._cache = {"s1": rule}

    res = client.post(
        CONTACT_URL.format(id=opp.id),
        json={"message": "Hello", "consent_fields": LEAD},
        cookies=auth_user_cookie(user),
    )
    assert res.status_code == 403


def test_my_opportunities_requires_auth(client, db_session):
    assert client.get(MY_OPP_URL).status_code == 401


# ---------------------------------------------------------------------------
# Admin outreach queue
# ---------------------------------------------------------------------------


def test_admin_outreach_queue_lists_pending(client, db_session):
    from tests.conftest import _make_admin, _make_user

    admin = _make_admin(db_session)
    user = _make_user(db_session)
    opp = _seed_opportunity(db_session, source_id="s1")
    from app.models.external_search import ProviderOutreach

    po = ProviderOutreach(
        opportunity_id=opp.id,
        requested_by_user_id=user.id,
        channel="PLATFORM_MESSAGE",
        outreach_status="PENDING",
    )
    db_session.add(po)
    db_session.flush()

    res = client.get(ADMIN_QUEUE_URL, cookies=auth_admin_cookie(admin))
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["outreach_status"] == "PENDING"
    assert rows[0]["requested_by_email"] == user.email
    assert rows[0]["approx_location"] == "Bristol city centre area"
    # No sensitive provider fields reach the queue read model.
    for forbidden in ("provider_contact_encrypted", "exact_address_encrypted", "source_url_encrypted"):
        assert forbidden not in rows[0]


def test_admin_outreach_queue_empty(client, db_session):
    from tests.conftest import _make_admin

    admin = _make_admin(db_session)
    res = client.get(ADMIN_QUEUE_URL, cookies=auth_admin_cookie(admin))
    assert res.status_code == 200
    assert res.json() == []


# ---------------------------------------------------------------------------
# Source registry (super admin)
# ---------------------------------------------------------------------------


def test_registry_lists_sources_for_super_admin(client, db_session):
    from tests.conftest import _make_admin

    admin = _make_admin(db_session, role="super_admin")
    res = client.get(ADMIN_REGISTRY_URL, cookies=auth_admin_cookie(admin))
    assert res.status_code == 200, res.text
    assert isinstance(res.json(), list)


# ---------------------------------------------------------------------------
# Section 16 metrics (super admin)
# ---------------------------------------------------------------------------

METRICS_URL = "/api/admin/external-search/metrics"


def test_metrics_requires_super_admin(client, db_session):
    from tests.conftest import _make_admin

    admin = _make_admin(db_session, role="admin")
    res = client.get(METRICS_URL, cookies=auth_admin_cookie(admin))
    assert res.status_code == 403


def test_metrics_endpoint_renders_section16(client, db_session):
    from tests.conftest import _make_admin

    admin = _make_admin(db_session, role="super_admin")
    res = client.get(METRICS_URL, cookies=auth_admin_cookie(admin))
    assert res.status_code == 200, res.text
    keys = set(res.json().keys())
    assert {
        "search_route_internal_only",
        "search_route_external_fallback",
        "internal_zero_result_rate",
        "provider_outreach_delivered",
        "provider_acceptance_rate",
        "claim_and_list_conversion",
        "lead_to_tenancy_rate",
        "verification_completion_rate",
        "circumvention_attempts",
        "source_takedown_or_complaints",
    } <= keys
