"""Anonymous website room search (POST /api/public/rooms/search).

Same internal-first waterfall as the signed-in assistant, with: no login, no
stored opportunities, a separate activation flag for external results, a
fixed set of public listing fields and per-visitor rate limiting.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.models.external_search import ExternalOpportunity
from app.models.feature_flag import FeatureFlag
from app.services.source_rights_registry import registry
from tests.test_search_orchestrator import _seed_listing

URL = "/api/public/rooms/search"
DEMO_RULE = {
    "demo": {"source_id": "demo", "tier": "B", "allow_fallback": True, "masking_permitted": True,
             "is_active": True, "permitted_fields": ["approx_location"]},
}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    registry._cache = None
    monkeypatch.setattr(settings, "public_room_search_rate_limit_max", 1000)
    yield
    registry._cache = None


def _flags(db, **values):
    for name, value in values.items():
        db.add(FeatureFlag(name=name, value=value, note="test", enabled_by="test"))
    db.flush()


def test_internal_listings_first_with_public_fields_only(client, db_session):
    _seed_listing(db_session, slug="listed-1", city="London")
    res = client.post(URL, json={"city": "London", "country": "United Kingdom", "q": "room"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["state"] == "INTERNAL_VERIFIED"
    assert body["internal_matches"] == 1
    row = body["internal_results"][0]
    assert row["slug"] == "listed-1" and row["currency"] == "GBP"
    assert not any(k.startswith("_") for k in row)
    assert body["external_matches"] == []


def test_no_external_results_unless_public_flag_is_on(client, db_session, external_activated):
    registry._cache = dict(DEMO_RULE)
    body = client.post(URL, json={"city": "Leeds", "country": "UK"}).json()
    assert body["state"] == "INTERNAL_ZERO"
    assert body["external_matches"] == []
    assert body["contact_note"] is None


def test_external_cards_are_masked_and_not_stored(client, db_session, external_activated):
    _flags(db_session, **{"external.public_search_fallback": True})
    registry._cache = dict(DEMO_RULE)
    before = db_session.query(ExternalOpportunity).count()

    body = client.post(URL, json={"city": "Leeds", "country": "UK"}).json()
    assert body["state"] == "EXTERNAL_DISCOVERED"
    card = body["external_matches"][0]
    assert card["opportunity_id"] is None
    assert card["last_seen_at"]  # discovery time stamped for unstored visitor cards
    assert "source_id" not in card and "source_tier" not in card
    assert "sign in" in body["contact_note"].lower()
    assert "have not been verified by Zoiko Rooms" in body["disclosure_text"]
    assert db_session.query(ExternalOpportunity).count() == before


@pytest.mark.parametrize("payload", [{"country": "UK"}, {"city": "Leeds"}, {"city": "", "country": "UK"},
                                     {"city": "Leeds", "country": "UK", "user_id": 1}])
def test_city_and_country_are_required(client, payload):
    assert client.post(URL, json=payload).status_code == 422


def test_rate_limited_per_ip_without_service_token(client, monkeypatch):
    monkeypatch.setattr(settings, "public_room_search_rate_limit_max", 2)
    codes = [client.post(URL, json={"city": "Leeds", "country": "UK"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_service_token_gives_each_visitor_their_own_budget(client, monkeypatch):
    monkeypatch.setattr(settings, "public_room_search_rate_limit_max", 1)
    monkeypatch.setattr(settings, "public_search_service_token", "website-secret")
    body = {"city": "Leeds", "country": "UK"}

    def call(token, visitor):
        return client.post(URL, json=body, headers={"X-Service-Token": token, "X-Visitor-Id": visitor}).status_code

    assert call("website-secret", "visitor-aaaa1111") == 200
    assert call("website-secret", "visitor-bbbb2222") == 200       # different visitor
    assert call("website-secret", "visitor-aaaa1111") == 429       # same visitor again
    # A visitor id without the right token cannot be used to dodge the IP limit.
    assert call("wrong-token", "visitor-cccc3333") == 200          # first IP call
    assert call("wrong-token", "visitor-dddd4444") == 429


def test_response_never_contains_contact_or_links(client, db_session, external_activated):
    _flags(db_session, **{"external.public_search_fallback": True})
    registry._cache = dict(DEMO_RULE)
    text = json.dumps(client.post(URL, json={"city": "Leeds", "country": "UK"}).json())
    # Field names quoted so the safe "has_exact_address": false flag doesn't match.
    for leak in ("http", "@", '"source_url"', '"provider_contact"', '"exact_address"'):
        assert leak not in text


def test_behind_a_proxy_each_forwarded_ip_has_its_own_budget(client, monkeypatch):
    monkeypatch.setattr(settings, "public_room_search_rate_limit_max", 1)
    body = {"city": "Leeds", "country": "UK"}

    def call(ip):
        return client.post(URL, json=body, headers={"X-Forwarded-For": f"{ip}, 10.0.0.1"}).status_code

    assert call("203.0.113.5") == 200
    assert call("203.0.113.9") == 200   # another visitor behind the same proxy
    assert call("203.0.113.5") == 429
