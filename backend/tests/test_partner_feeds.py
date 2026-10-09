"""Phase 4 tests -- partner feed adapter (ZR-AI-SEARCH-001 Section 13).

Tier A structured feeds are ingested only for registered PARTNER_FEED
sources with legal + security approvals; per-field data is honoured via
permitted_fields; ingestion dedupes by a content hash and is audited.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.external_search import ExternalOpportunity, SourceRightRegistry
from app.services.partner_feeds import partner_feed_adapter


def _add_source(db, **kw):
    defaults = dict(
        source_id="partner1",
        source_name_internal="Partner Feed One",
        territories=["GB"],
        acquisition_mode="PARTNER_FEED",
        legal_approved=True,
        security_approved=True,
        status="ACTIVE",
        display_permitted=True,
        masking_permitted=True,
        cache_ttl_seconds=3600,
        permitted_fields=["provider_name", "approx_location"],
        source_brand_display_rule="Example Partner",
    )
    defaults.update(kw)
    src = SourceRightRegistry(**defaults)
    db.add(src)
    db.flush()
    return src


def _external_id_item(external_id="E-1"):
    return {
        "external_id": external_id,
        "provider_name": "Riverside Properties",
        "approx_location": "Greenwich, London",
        "price_minor": 240000,
        "currency": "GBP",
        "room_type": "1BHK",
        "source_url": "https://feeds.example.com/listings/E-1",
    }


class TestIngestGating:
    def test_unregistered_source_blocked(self, db_session):
        with pytest.raises(PermissionError):
            partner_feed_adapter.ingest_feed(
                db_session, source_id="ghost", items=[_external_id_item()]
            )

    def test_requires_partner_feed_mode(self, db_session):
        _add_source(db_session, acquisition_mode="LICENSED_API", source_id="manual")
        with pytest.raises(PermissionError):
            partner_feed_adapter.ingest_feed(
                db_session, source_id="manual", items=[_external_id_item()]
            )

    def test_requires_approvals(self, db_session):
        _add_source(db_session, security_approved=False)
        with pytest.raises(PermissionError):
            partner_feed_adapter.ingest_feed(
                db_session, source_id="partner1", items=[_external_id_item()]
            )


class TestIngestBehavior:
    def test_items_ingested_as_unverified_external(self, db_session):
        _add_source(db_session)
        created = partner_feed_adapter.ingest_feed(
            db_session, source_id="partner1", items=[_external_id_item("E-1")]
        )
        assert len(created) == 1
        opp = created[0]
        assert opp.status == "EXTERNAL_DISCOVERED"
        assert opp.verification_status == "NOT_VERIFIED_BY_ZOIKO_ROOMS"
        assert opp.source_id == "partner1"
        assert opp.provider_name == "Riverside Properties"
        assert opp.approx_location == "Greenwich, London"
        assert opp.source_domain == "Example Partner"
        assert opp.permitted_features == ["approx_location", "provider_name"]

    def test_unpermitted_fields_not_copied(self, db_session):
        _add_source(db_session, permitted_fields=["provider_name"])
        created = partner_feed_adapter.ingest_feed(
            db_session, source_id="partner1", items=[_external_id_item("E-1")]
        )
        assert created[0].provider_name == "Riverside Properties"
        assert created[0].approx_location is None

    def test_reingesting_same_feed_is_noop(self, db_session):
        _add_source(db_session)
        first = partner_feed_adapter.ingest_feed(
            db_session, source_id="partner1", items=[_external_id_item("E-1")]
        )
        second = partner_feed_adapter.ingest_feed(
            db_session, source_id="partner1", items=[_external_id_item("E-1")]
        )
        assert len(first) == 1
        assert len(second) == 0

    def test_items_without_external_id_skipped(self, db_session):
        _add_source(db_session)
        created = partner_feed_adapter.ingest_feed(
            db_session, source_id="partner1", items=[{"provider_name": "no id"}]
        )
        assert created == []

    def test_different_external_ids_both_ingested(self, db_session):
        _add_source(db_session)
        created = partner_feed_adapter.ingest_feed(
            db_session,
            source_id="partner1",
            items=[_external_id_item("E-1"), _external_id_item("E-2")],
        )
        assert len(created) == 2


class TestAvailabilitySync:
    def test_sync_marks_rows_refreshed(self, db_session):
        _add_source(db_session)
        partner_feed_adapter.ingest_feed(db_session, source_id="partner1", items=[_external_id_item("E-1")])
        n = partner_feed_adapter.sync_availability(db_session, source_id="partner1")
        assert n == 1
        opp = db_session.scalars(
            select(ExternalOpportunity).where(ExternalOpportunity.source_id == "partner1")
        ).one()
        assert "availability_checked_at" in opp.raw_data
        assert opp.raw_data["ttl_seconds"] == 3600


class TestFlagIndependence:
    def test_partner_feed_gated_on_approvals_not_billing_flag(self, db_session):
        _add_source(db_session)
        created = partner_feed_adapter.ingest_feed(
            db_session, source_id="partner1", items=[_external_id_item("E-1")]
        )
        assert len(created) == 1