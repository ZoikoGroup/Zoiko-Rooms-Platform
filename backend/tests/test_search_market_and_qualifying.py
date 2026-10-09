"""Section 13 market activation, Section 5.1 qualifying rules, SRCH-14/15 and
the Section 15 envelope.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models.audit import AuditEvent
from app.models.booking import Booking
from app.models.external_search import ExternalMarketLegalPack, ExternalOpportunity
from app.models.guest import Guest
from app.services import market_legal_pack as mlp
from app.services.search_orchestrator import SearchOrchestrator, SearchQuery, persist_external_cards
from app.services.source_rights_registry import registry
from tests.conftest import _make_user
from tests.test_search_orchestrator import _seed_listing

DEMO_RULE = {
    "demo": {"source_id": "demo", "tier": "B", "allow_fallback": True, "masking_permitted": True,
             "is_active": True, "permitted_fields": ["approx_location"]},
}


@pytest.fixture(autouse=True)
def _reset():
    registry._cache = None
    yield
    registry._cache = None


def _search(db, **kw):
    kw.setdefault("country", "United Kingdom")
    kw.setdefault("q", "room")
    return SearchOrchestrator().search(db, SearchQuery(**kw), correlation_id="c")


# -- Section 13: market legal pack ------------------------------------------------------

@pytest.mark.usefixtures("external_activated")
class TestMarketPackGate:
    def test_no_pack_for_the_market_means_no_external_search(self, db_session):
        registry._cache = dict(DEMO_RULE)
        result = _search(db_session, city="Toronto", country="Canada")
        assert result.discovery.state == "INTERNAL_ZERO"
        assert result.discovery.external_search_status == "NOT_ACTIVATED"

    def test_unapproved_or_suspended_pack_fails_closed(self, db_session):
        pack = db_session.query(ExternalMarketLegalPack).filter_by(market_code="GB").one()
        for change in ({"privacy_approved": False}, {"status": "SUSPENDED"}, {"external_search_enabled": False}):
            pack.privacy_approved, pack.status, pack.external_search_enabled = True, "ACTIVE", True
            for k, v in change.items():
                setattr(pack, k, v)
            db_session.flush()
            assert mlp.active_pack(db_session, "GB") is None or not mlp.external_search_allowed(db_session, "GB")

    def test_latest_active_version_wins(self, db_session):
        db_session.add(ExternalMarketLegalPack(
            market_code="GB", version=2, status="ACTIVE", legal_approved=True, privacy_approved=True,
            commercial_approved=True, external_search_enabled=False,
        ))
        db_session.flush()
        assert mlp.active_pack(db_session, "United Kingdom").version == 2
        assert mlp.external_search_allowed(db_session, "GB") is False

    def test_public_visitors_need_their_own_permission(self, db_session):
        pack = db_session.query(ExternalMarketLegalPack).filter_by(market_code="GB").one()
        pack.public_visitor_search_enabled = False
        db_session.flush()
        registry._cache = dict(DEMO_RULE)
        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="room", city="Leeds", country="UK"), public_visitor=True
        )
        assert result.discovery.external_search_status == "NOT_ACTIVATED"
        assert _search(db_session, city="Leeds").discovery.state == "EXTERNAL_DISCOVERED"


# -- Section 5.1: qualifying matches ----------------------------------------------------

class TestQualifying:
    def test_city_is_matched_exactly(self, db_session):
        _seed_listing(db_session, slug="ny-1", city="New York")
        assert _search(db_session, city="York").discovery.internal_matches == 0
        assert _search(db_session, city="new york").discovery.internal_matches == 1

    def test_listing_booked_for_the_move_in_date_does_not_qualify(self, db_session):
        listing = _seed_listing(db_session, slug="booked-1", city="Leeds")
        guest = Guest(id="G-DATES", name="G", email="g-dates@test.com", joined_at=date.today())
        db_session.add(guest)
        start = date.today() + timedelta(days=10)
        db_session.add(Booking(id="BK-DATES", listing_id=listing.id, guest_id=guest.id, check_in=start,
                               check_out=start + timedelta(days=60), guests=1, status="confirmed"))
        db_session.flush()
        assert _search(db_session, city="Leeds", move_in_from=start + timedelta(days=5)).discovery.internal_matches == 0
        assert _search(db_session, city="Leeds", move_in_from=start + timedelta(days=90)).discovery.internal_matches == 1
        assert _search(db_session, city="Leeds").discovery.internal_matches == 1

    def test_radius_keeps_nearby_listings_only(self, db_session):
        near = _seed_listing(db_session, slug="near-1", city="London")
        far = _seed_listing(db_session, slug="far-1", city="London")
        near.latitude, near.longitude = 51.5074, -0.1278      # central London
        far.latitude, far.longitude = 51.7520, -1.2577        # Oxford-ish
        db_session.flush()
        result = _search(db_session, city="London", latitude=51.5080, longitude=-0.1281, radius_m=5000)
        assert [r["slug"] for r in result.internal_results] == ["near-1"]


# -- SRCH-14 ------------------------------------------------------------------------------

@pytest.mark.usefixtures("external_activated")
class TestFairHousing:
    def test_protected_terms_in_filters_are_rejected(self, db_session):
        result = _search(db_session, city="Leeds", objective_filters=["furnished", "female only"])
        assert result.discovery.state == "BLOCKED"
        assert result.discovery.external_search_status == "BLOCKED_PROHIBITED_CRITERIA"

    def test_market_pack_terms_extend_the_vocabulary(self, db_session):
        assert _search(db_session, city="Leeds", q="no dss").discovery.external_search_status != "BLOCKED_PROHIBITED_CRITERIA"
        pack = db_session.query(ExternalMarketLegalPack).filter_by(market_code="GB").one()
        pack.prohibited_search_terms = ["no dss"]
        db_session.flush()
        assert _search(db_session, city="Leeds", q="no dss").discovery.external_search_status == "BLOCKED_PROHIBITED_CRITERIA"

    def test_place_names_containing_terms_are_not_blocked(self, db_session):
        assert _search(db_session, city="Whitechapel").discovery.external_search_status != "BLOCKED_PROHIBITED_CRITERIA"


# -- Section 4.1 dedupe, Section 15 envelope, SRCH-15 attribution ----------------------------

@pytest.mark.usefixtures("external_activated")
class TestEnvelopeAndAttribution:
    def test_internal_route_envelope(self, db_session):
        _seed_listing(db_session, slug="env-1", city="Leeds")
        disc = _search(db_session, city="Leeds").discovery
        assert disc.search_route == "INTERNAL_ONLY"
        assert disc.external_search_status == "SKIPPED_INTERNAL_MATCH"
        assert disc.query_id.startswith("q_")

    def test_external_route_envelope_and_card_fields(self, db_session):
        registry._cache = dict(DEMO_RULE)
        user = _make_user(db_session)
        result = _search(db_session, city="Leeds")
        assert result.discovery.search_route == "EXTERNAL_FALLBACK"
        assert result.discovery.external_search_status == "COMPLETE"
        card = persist_external_cards(db_session, result.discovery.external_matches, user.id, result.external_private)[0]
        assert card.external_opportunity_id.startswith("ext_")
        assert card.status == "EXTERNAL_DISCOVERED"
        assert card.verification_status == "NOT_VERIFIED_BY_ZOIKO_ROOMS"
        assert card.primary_cta == "REQUEST_ZOIKO_CONTACT"
        assert card.discovered_at is not None
        opp = db_session.get(ExternalOpportunity, card.opportunity_id)
        assert opp.market_code == "GB"

    def test_search_audit_rows_name_the_user(self, db_session):
        user = _make_user(db_session)
        SearchOrchestrator().search(
            db_session, SearchQuery(q="room", city="Leeds", country="UK"), correlation_id="c-attr", actor_id=user.id
        )
        rows = db_session.query(AuditEvent).filter(AuditEvent.correlation_id == "c-attr").all()
        assert rows and all(f"actor=user:{user.id}" in r.reason for r in rows)
