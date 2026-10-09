"""Licensed listing APIs (Section 6.1 Tier B) through the search waterfall.

HTTP is mocked; no real provider is called. Covers: registry/broker gating,
market and city scoping, permitted-field masking, contact persistence rules,
internal-only registration for non-displayable sources and result caching.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.models.external_search import ExternalOpportunity, SourceRightRegistry
from app.services import external_providers
from app.services import source_rights_registry as srr_mod
from app.services.external_search_crypto import decrypt_contact
from app.services.search_orchestrator import SearchOrchestrator, SearchQuery, persist_external_cards
from app.services.source_rights_registry import registry

pytestmark = pytest.mark.usefixtures("external_activated")

RENTCAST_ROWS = [
    {
        "id": "rc-1",
        "formattedAddress": "1200 Barton Springs Rd, Austin, TX 78704",
        "city": "Austin",
        "state": "TX",
        "propertyType": "Apartment",
        "bedrooms": 1,
        "price": 1650,
        "listingAgent": {"name": "Jo Agent", "phone": "512-555-0100", "email": "jo@agency.example"},
    },
    {
        "id": "rc-2",
        "formattedAddress": "77 Rainey St, Austin, TX 78701",
        "city": "Austin",
        "state": "TX",
        "propertyType": "Condo",
        "bedrooms": 2,
        "price": 2400,
    },
]

DOMAIN_ROWS = [
    {
        "type": "PropertyListing",
        "listing": {
            "id": 2019876543,
            "listingSlug": "12-campbell-parade-bondi-beach-nsw-2026-2019876543",
            "priceDetails": {"price": 650, "displayPrice": "$650 per week"},
            "propertyDetails": {
                "suburb": "Bondi Beach",
                "state": "NSW",
                "propertyType": "ApartmentUnitFlat",
                "bedrooms": 1,
                "displayableAddress": "12 Campbell Parade, Bondi Beach",
            },
            "advertiser": {"name": "Bondi Realty", "contacts": [{"name": "Sam", "phone": "02 9000 0000"}]},
        },
    }
]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    registry._cache = None
    external_providers.clear_cache()
    monkeypatch.setattr(settings, "rentcast_api_key", "test-rentcast-key")
    monkeypatch.setattr(settings, "domain_api_key", "test-domain-key")
    yield
    registry._cache = None
    external_providers.clear_cache()


@pytest.fixture()
def http_calls(monkeypatch):
    """Route provider HTTP to canned responses and record every request."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "api.rentcast.io":
            return httpx.Response(200, json=RENTCAST_ROWS)
        if request.url.host == "api.domain.com.au":
            return httpx.Response(200, json=DOMAIN_ROWS)
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(
        external_providers.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    return calls


def _source(db, source_id, *, status="ACTIVE", approved=True, display=True, contact=False,
            permitted=("approx_location", "advertised_price", "room_type"), clickthrough=False):
    row = SourceRightRegistry(
        source_id=source_id,
        source_name_internal=source_id,
        territories=[],
        acquisition_mode="LICENSED_API",
        legal_approved=approved,
        security_approved=approved,
        status=status,
        display_permitted=display,
        masking_permitted=not clickthrough,
        clickthrough_required=clickthrough,
        permitted_fields=list(permitted),
        contact_extraction_permitted=contact,
        outreach_permitted=contact,
        outreach_channels=["EMAIL"] if contact else [],
        cache_ttl_seconds=3600,
    )
    db.add(row)
    db.flush()
    rules = dict(registry._cache or {})
    if status == "ACTIVE":
        rules[source_id] = srr_mod._map_db_row(row)
    registry._cache = rules
    return row


def _search(db, city="Austin", country="US"):
    return SearchOrchestrator().search(db, SearchQuery(q="room", city=city, country=country))


def test_rentcast_results_become_masked_cards(db_session, http_calls):
    _source(db_session, "rentcast")
    result = _search(db_session)

    assert result.discovery.state == "EXTERNAL_DISCOVERED"
    cards = result.discovery.external_matches
    assert len(cards) == 2
    assert {c.location_city for c in cards} == {"Austin"}
    assert sorted(c.rent_monthly for c in cards) == [1650, 2400]
    assert all(c.title == "[External listing — masked]" for c in cards)
    dumped = json.dumps([c.model_dump(mode="json") for c in cards])
    for leak in ("Barton Springs", "Rainey", "Jo Agent", "512-555", "agency.example", "rc-1"):
        assert leak not in dumped
    assert http_calls[0].headers["X-Api-Key"] == "test-rentcast-key"
    assert http_calls[0].url.params["city"] == "Austin"


def test_persisted_opportunity_encrypts_source_data_and_drops_unpermitted_contact(db_session, http_calls):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    _source(db_session, "rentcast", contact=False)
    result = _search(db_session)
    out = persist_external_cards(db_session, result.discovery.external_matches, user.id, result.external_private)

    opp = db_session.get(ExternalOpportunity, out[0].opportunity_id)
    assert opp.source_id == "rentcast"
    assert opp.advertised_price_currency == "USD" and opp.price_period == "MONTH"
    assert "Barton Springs" not in (opp.exact_address_encrypted or "")
    assert decrypt_contact(opp.exact_address_encrypted).startswith("1200 Barton Springs")
    # contact_extraction_permitted is false for this source, so no contact is kept.
    assert opp.provider_contact_encrypted is None
    assert "source_id" not in out[0].model_dump()


def test_contact_kept_encrypted_only_when_permitted(db_session, http_calls):
    from tests.conftest import _make_user

    user = _make_user(db_session)
    _source(db_session, "rentcast", contact=True)
    result = _search(db_session)
    out = persist_external_cards(db_session, result.discovery.external_matches, user.id, result.external_private)
    opps = [db_session.get(ExternalOpportunity, c.opportunity_id) for c in out]
    stored = {decrypt_contact(o.provider_contact_encrypted) for o in opps}
    assert "jo@agency.example" in stored
    assert all("jo@agency.example" not in (o.provider_contact_encrypted or "") for o in opps)


def test_unpermitted_fields_are_not_displayed(db_session, http_calls):
    _source(db_session, "rentcast", permitted=())
    cards = _search(db_session).discovery.external_matches
    assert cards and all(c.location_city is None and c.rent_monthly is None and c.room_type is None for c in cards)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "REVIEW"},
        {"approved": False},
    ],
)
def test_unapproved_source_is_never_called(db_session, http_calls, kwargs):
    _source(db_session, "rentcast", **kwargs)
    result = _search(db_session)
    assert http_calls == []
    assert result.discovery.external_matches == []


def test_provider_only_called_for_its_market_and_with_a_city(db_session, http_calls):
    _source(db_session, "rentcast")
    _search(db_session, city="London", country="GB")
    _search(db_session, city=None, country="US")
    assert http_calls == []


def test_missing_api_key_means_no_call(db_session, http_calls, monkeypatch):
    monkeypatch.setattr(settings, "rentcast_api_key", "")
    _source(db_session, "rentcast")
    assert _search(db_session).discovery.external_matches == []
    assert http_calls == []


def test_results_are_cached_for_the_source_ttl(db_session, http_calls):
    _source(db_session, "rentcast")
    _search(db_session)
    _search(db_session)
    assert len(http_calls) == 1


def test_provider_error_returns_no_results(db_session, monkeypatch):
    def handler(request):
        return httpx.Response(500)

    real_client = httpx.Client
    monkeypatch.setattr(
        external_providers.httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    _source(db_session, "rentcast")
    result = _search(db_session)
    assert result.discovery.external_matches == []


def test_clickthrough_source_is_registered_internally_never_shown(db_session, http_calls):
    """Domain-style terms (attribution + link back) fail the masked-display
    gate (SRCH-07): results become internal opportunities only."""
    _source(db_session, "domain_au", display=True, clickthrough=True)
    result = _search(db_session, city="Bondi Beach", country="Australia")

    assert result.discovery.external_matches == []
    assert result.discovery.state == "BLOCKED"
    opps = db_session.query(ExternalOpportunity).filter_by(source_id="domain_au").all()
    assert len(opps) == 1
    assert opps[0].discovered_by_user_id is None
    assert opps[0].price_period == "WEEK"
    assert decrypt_contact(opps[0].source_url_encrypted).startswith("https://www.domain.com.au/")

    # A repeat search does not duplicate the internal opportunity.
    external_providers.clear_cache()
    _search(db_session, city="Bondi Beach", country="Australia")
    assert db_session.query(ExternalOpportunity).filter_by(source_id="domain_au").count() == 1


def test_weekly_price_converted_to_monthly_for_cards():
    cand = external_providers.ExternalCandidate(
        source_id="domain_au", external_id="1", advertised_price_minor=65000, price_period="WEEK"
    )
    assert cand.rent_monthly == 2817
