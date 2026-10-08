"""SRCH-15 -- auditability of the external search protocol.

Every external search decision (waterfall, deeply, fallback, fair-housing
block) and every outreach decision is recorded on the append-only AuditEvent
chain with action, resource_type, correlation_id and reason.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditEvent
from app.services.commercial_policy import commercial_policy_service
from app.services.search_orchestrator import SearchOrchestrator, SearchQuery
from app.services.source_rights_registry import registry


def _events(db, *, action=None, resource_id=None):
    q = select(AuditEvent).order_by(AuditEvent.id.desc())
    if action:
        q = q.where(AuditEvent.action == action)
    if resource_id:
        # Search rows are keyed by query_id; the caller's correlation id ties
        # them to the request.
        q = q.where(AuditEvent.correlation_id == resource_id)
    return list(db.execute(q).scalars())


def _seed_rule(db):
    from app.models.external_search import SourceRightRegistry

    row = SourceRightRegistry(
        source_id="s1",
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
        "s1": {
            "source_id": "s1",
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


@pytest.mark.usefixtures("external_activated")
class TestSearchAudited:
    def test_fallback_search_audited_discovered(self, db_session):
        _seed_rule(db_session)
        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="en-suite room", city="London", country="United Kingdom"), correlation_id="c-1"
        )
        assert result.discovery.state == "EXTERNAL_DISCOVERED"
        events = _events(db_session, action="search_external.discovered", resource_id="c-1")
        assert len(events) == 1
        # SRCH-15: route, disclosure version, actor and market are all recorded.
        reason = events[0].reason
        assert reason.startswith("external_discovered:1;disclosure=ZR-AI-SEARCH-001/1.0:7.2")
        assert "actor=system" in reason and "market=GB" in reason

    def test_internal_waterfall_audited(self, db_session):
        from app.models.listing import Listing

        db_session.add(
            Listing(
                id="listed-1", slug="listed-1", name="Bright En-suite Room",
                property_type="private_room", room_type="ensuite",
                city="London", location="Central", price_per_night=900,
                currency="GBP", state="PUBLISHED", guests=1,
                min_stay_nights=30, description="A room.",
            )
        )
        db_session.flush()
        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="en-suite room", city="London", country="United Kingdom"), correlation_id="c-2"
        )
        assert result.discovery.state == "INTERNAL_VERIFIED"
        events = _events(db_session, action="search_external.waterfall", resource_id="c-2")
        assert len(events) == 1
        reason = events[0].reason
        assert reason.startswith("internal_only:1;disclosure=ZR-AI-SEARCH-001/1.0:7.1")
        assert "actor=system" in reason and "market=GB" in reason

    def test_fair_housing_block_audited(self, db_session):
        _seed_rule(db_session)
        result = SearchOrchestrator().search(
            db_session, SearchQuery(q="christian only", city="London", country="United Kingdom"), correlation_id="c-3"
        )
        assert result.discovery.state == "BLOCKED"
        events = _events(db_session, action="search_external.fair_housing_blocked", resource_id="c-3")
        assert len(events) == 1


class TestOutreachAudited:
    def test_commercial_policy_decision_audited(self, db_session):
        commercial_policy_service.charge_eligible(
            db_session, market_code="GB", provider_type="LANDLORD"
        )
        events = db_session.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "commercial_policy.blocked")
            .order_by(AuditEvent.id.desc())
        ).all()
        assert len(events) == 1
        assert events[0].reason == "feature_flag_off"