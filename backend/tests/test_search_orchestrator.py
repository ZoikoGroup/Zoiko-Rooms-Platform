"""SRCH-01 / SRCH-02 / SRCH-14 -- orchestrator waterfall and fair housing.

* SRCH-01: internal verified inventory always searched first.
* SRCH-02: external fallback ONLY when internal matches == 0.
* SRCH-14: submissions describing a protected class never reach external
  fallback (returned BLOCKED with a guardrail note and an audit row).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.services.search_orchestrator import SearchOrchestrator, SearchQuery
from app.services.source_rights_registry import registry

# External discovery and outreach are off until market activation (Section 13).
pytestmark = pytest.mark.usefixtures("external_activated")


@pytest.fixture(autouse=True)
def _reset_registry_cache():
    registry._cache = None
    yield
    registry._cache = None


def _seed_listing(db, *, slug="listed-1", city="London", price=900, name="Bright En-suite Room",
                  room_type="ensuite"):
    from app.models.listing import Listing

    listing = Listing(
        id=slug,
        slug=slug,
        name=name,
        property_type="private_room",
        room_type=room_type,
        city=city,
        location="Central London",
        latitude=None,
        longitude=None,
        price_per_night=price,
        currency="GBP",
        state="PUBLISHED",
        guests=1,
        min_stay_nights=30,
        description="A bright ensuite room near the station.",
    )
    db.add(listing)
    db.flush()
    return listing


def _seed_rule(db, source_id="s1"):
    from app.models.external_search import SourceRightRegistry

    row = SourceRightRegistry(
        source_id=source_id,
        source_name_internal="Example Org",
        territories=["GB"],
        acquisition_mode="PUBLIC_FETCH",
        legal_approved=True,
        security_approved=True,
        status="ACTIVE",
        display_permitted=True,
        outreach_permitted=True,
        contact_extraction_permitted=True,
        masking_permitted=True,
        permitted_fields=["provider_name", "approx_location"],
        cache_ttl_seconds=3600,
        source_brand_display_rule="Test",
    )
    db.add(row)
    db.flush()
    registry._cache = {
        source_id: {
            "source_id": source_id,
            "domain": "example.org",
            "tier": "B",
            "allow_fallback": True,
            "allow_direct_contact": True,
            "allow_indexing": False,
            "policy_ref": "ZR-POL-SRCH-001",
            "clickthrough_required": False,
            "masking_permitted": True,
            "outreach_channels": ["EMAIL"],
            "notes": None,
            "is_active": True,
        }
    }


class TestWaterfall:
    def test_internal_first_when_matches(self, db_session):
        _seed_rule(db_session)
        _seed_listing(db_session)

        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="en-suite room", city="London")
        )
        assert result.discovery.state == "INTERNAL_VERIFIED"
        assert result.discovery.fallback_triggered is False
        assert result.discovery.external_matches == []
        assert len(result.internal_results) == 1

    def test_external_fallback_when_zero_internal(self, db_session):
        _seed_rule(db_session)

        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="en-suite room", city="London")
        )
        assert result.discovery.state == "EXTERNAL_DISCOVERED"
        assert result.discovery.fallback_triggered is True
        assert len(result.discovery.external_matches) == 1
        match = result.discovery.external_matches[0]
        assert match.verification_status == "unverified"
        assert match.is_unlocked is False

    def test_internal_without_city_match_triggers_fallback(self, db_session):
        # Internal inventories exist but not for the queried city, so internal
        # matches == 0 and the external fallback is allowed.
        _seed_rule(db_session)
        _seed_listing(db_session, slug="listed-2", city="Manchester", name="Bright En-suite Room")

        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="en-suite room", city="London")
        )
        assert result.discovery.internal_matches == 0
        assert result.discovery.fallback_triggered is True
        assert result.discovery.state == "EXTERNAL_DISCOVERED"


class TestFairHousing:
    @pytest.mark.parametrize(
        "q",
        [
            "christian only rooms",
            "rooms for muslims",
            "women only flatmate",
            "no children please",
            "young graduate students",
        ],
    )
    def test_protected_characteristic_blocks(self, db_session, q):
        _seed_rule(db_session)
        result = SearchOrchestrator().search(
            db_session, SearchQuery(q=q, city="London")
        )
        assert result.discovery.state == "BLOCKED"
        assert result.discovery.external_matches == []
        assert any("FAIR_HOUSING" in note for note in result.discovery.guardrail_notes)

    def test_city_with_protected_class_blocks(self, db_session):
        _seed_rule(db_session)
        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="room", city="Christian Quarter")
        )
        assert result.discovery.state == "BLOCKED"

    def test_benign_query_passes_fair_housing(self, db_session):
        _seed_rule(db_session)
        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="bright ensuite room", city="London")
        )
        assert result.discovery.state == "EXTERNAL_DISCOVERED"

    def test_fair_housing_blocks_before_internal(self, db_session):
        _seed_rule(db_session)
        _seed_listing(db_session)
        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="disabled only", city="London")
        )
        assert result.discovery.state == "BLOCKED"


class TestInternalQualifyingMatches:
    """ZR-AI-SEARCH-001 Section 5.1 qualifying-match gate."""

    def test_publication_state_is_the_only_qualifying_state(self, db_session):
        from app.models.listing import Listing

        _seed_rule(db_session)
        _seed_listing(db_session)
        paused = Listing(
            id="paused-1",
            slug="paused-1",
            name="Paused Room",
            property_type="private_room",
            room_type="private_room",
            city="London",
            location="Central",
            price_per_night=900,
            currency="GBP",
            state="PAUSED",  # risk-hold / paused is non-qualifying
            guests=1,
            min_stay_nights=30,
        )
        db_session.add(paused)
        db_session.flush()

        result = SearchOrchestrator().search(db_session, SearchQuery(city="London"))
        assert result.discovery.state == "INTERNAL_VERIFIED"
        assert result.discovery.internal_matches == 1
        assert all(r["state"] == "PUBLISHED" for r in result.internal_results)

    def test_room_type_filter_excludes_non_matching(self, db_session):
        _seed_rule(db_session)
        _seed_listing(db_session, slug="suite-1", city="London")
        _seed_listing(db_session, slug="studio-1", city="London", room_type="studio")
        result = SearchOrchestrator().search(
            db_session, SearchQuery(city="London", room_type="ensuite")
        )
        assert result.discovery.state == "INTERNAL_VERIFIED"
        assert [r["id"] for r in result.internal_results] == ["suite-1"]
        assert all(r["roomType"].lower() == "ensuite" for r in result.internal_results)

        no_match = SearchOrchestrator().search(
            db_session, SearchQuery(city="London", room_type="shared_house")
        )
        assert no_match.discovery.internal_matches == 0
        assert no_match.discovery.state == "EXTERNAL_DISCOVERED"

    def test_objective_filters_require_all_amenities(self, db_session):
        _seed_rule(db_session)
        _seed_listing(db_session, slug="furn-1", city="London")
        from app.models.listing import Listing

        listing = db_session.get(Listing, "furn-1")
        listing.amenities = ["Furnished", "WiFi"]
        db_session.flush()

        hit = SearchOrchestrator().search(
            db_session,
            SearchQuery(city="London", objective_filters=["Furnished", "WiFi"]),
        )
        assert hit.discovery.internal_matches == 1

        miss = SearchOrchestrator().search(
            db_session,
            SearchQuery(city="London", objective_filters=["Furnished", "Parking"]),
        )
        assert miss.discovery.internal_matches == 0
        assert miss.discovery.state == "EXTERNAL_DISCOVERED"

    def test_price_ceiling_is_a_qualifying_filter(self, db_session):
        _seed_rule(db_session)
        _seed_listing(db_session, slug="cheap-1", city="London", price=600)
        result = SearchOrchestrator().search(
            db_session, SearchQuery(city="London", max_price=700)
        )
        assert result.discovery.internal_matches == 1


class TestInternalRanking:
    """ZR-AI-SEARCH-001 Section 5.2 ranking: relevance -> freshness -> trust ->
    user-fit -> stable tie-break."""

    def test_fresh_listing_ranks_above_stale(self, db_session):
        from datetime import datetime, timedelta, timezone

        from app.models.listing import Listing

        _seed_rule(db_session)
        fresh = _seed_listing(db_session, slug="fresh-1", city="London")
        fresh.availability_confirmed_at = datetime.now(timezone.utc)
        stale = _seed_listing(db_session, slug="stale-1", city="London", name="Older Room")
        stale.availability_confirmed_at = datetime.now(timezone.utc) - timedelta(days=60)
        db_session.flush()

        result = SearchOrchestrator().search(db_session, SearchQuery(city="London"))
        assert [r["id"] for r in result.internal_results] == ["fresh-1", "stale-1"]
        assert result.internal_results[0]["availabilityFreshStale"] is False
        assert result.internal_results[1]["availabilityFreshStale"] is True

    def test_never_confirmed_ranks_below_fresh_and_is_stale(self, db_session):
        from datetime import datetime, timezone

        _seed_rule(db_session)
        confirmed = _seed_listing(db_session, slug="confirmed-1", city="London")
        confirmed.availability_confirmed_at = datetime.now(timezone.utc)
        _seed_listing(db_session, slug="unknown-1", city="London", name="Never Confirmed Room")
        db_session.flush()

        result = SearchOrchestrator().search(db_session, SearchQuery(city="London"))
        assert [r["id"] for r in result.internal_results] == ["confirmed-1", "unknown-1"]
        assert result.internal_results[1]["availabilityConfirmedAt"] is None
        assert result.internal_results[1]["availabilityFreshStale"] is True

    def test_deterministic_tie_break_by_id(self, db_session):
        _seed_rule(db_session)
        _seed_listing(db_session, slug="b-room", city="London")
        _seed_listing(db_session, slug="a-room", city="London")
        db_session.flush()

        result = SearchOrchestrator().search(db_session, SearchQuery(city="London"))
        assert [r["id"] for r in result.internal_results] == ["a-room", "b-room"]
        # stable between calls -- never a random reshuffle of material ranking
        again = SearchOrchestrator().search(db_session, SearchQuery(city="London"))
        assert [r["id"] for r in again.internal_results] == ["a-room", "b-room"]

    def test_confirm_availability_stamps_and_audits(self, db_session):
        from app.models.audit import AuditEvent
        from app.models.listing import Listing

        from app.services.search_orchestrator import orchestrator

        _seed_listing(db_session)
        orchestrator.confirm_availability(db_session, "listed-1", confirmed_by_party_id=42)
        listing = db_session.get(Listing, "listed-1")
        assert listing.availability_confirmed_at is not None
        events = db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == "availability.confirmed")
        ).all()
        assert len(events) == 1
        assert "by_party=42" in events[0].reason

    def test_confirm_availability_rejects_non_published(self, db_session):
        from app.models.listing import Listing

        from app.services.search_orchestrator import orchestrator

        _seed_listing(db_session, slug="paused-x")
        listing = db_session.get(Listing, "paused-x")
        listing.state = "PAUSED"
        db_session.flush()

        with pytest.raises(PermissionError):
            orchestrator.confirm_availability(db_session, "paused-x")