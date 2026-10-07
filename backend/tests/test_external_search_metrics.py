"""ZR-AI-SEARCH-001 Section 16 -- audit, observability and commercial metrics.

Metrics are computed on demand from the audit chain plus the opportunity /
outreach tables through the super-admin endpoint. Rates return None on a zero
denominator rather than a fabricated 0%.
"""

from __future__ import annotations

from app.crud.audit import log_audit_event
from app.models.external_search import ExternalOpportunity, ProviderOutreach
from app.services.external_search_metrics import external_search_metrics

from tests.conftest import _make_user


def _seed_opportunity(db, *, status="EXTERNAL_DISCOVERED", verification="NOT_VERIFIED_BY_ZOIKO_ROOMS", idx=1):
    opp = ExternalOpportunity(
        external_opportunity_id=f"ext-m{idx}",
        source_id="s1",
        status=status,
        verification_status=verification,
        approx_location="Bristol city centre area",
    )
    db.add(opp)
    db.flush()
    return opp


def _seed_outreach(db, opp, *, response=None, status="SENT"):
    user = _make_user(db)
    po = ProviderOutreach(
        opportunity_id=opp.id,
        requested_by_user_id=user.id,
        channel="PLATFORM_MESSAGE",
        outreach_status=status,
        provider_response=response,
    )
    db.add(po)
    db.flush()
    return po


def _audit(db, action, resource_id="search-1", reason=""):
    log_audit_event(
        db,
        actor=None,
        action=action,
        resource_type="search" if resource_id.startswith("search") else "external_opportunity",
        resource_id=resource_id,
        correlation_id="c-metrics",
        reason=reason,
    )


class TestSection16Metrics:
    def test_empty_db_returns_none_rates_not_zeros(self, db_session):
        m = external_search_metrics(db_session, days=30)
        assert m["search_route_internal_only"] == 0
        assert m["internal_zero_result_rate"] is None
        assert m["provider_acceptance_rate"] is None
        assert m["claim_and_list_conversion"] is None
        assert m["lead_to_tenancy_rate"] is None
        assert m["lead_to_tenancy_note"]

    def test_zero_result_rate_from_route_counts(self, db_session):
        _audit(db_session, "search_external.waterfall", reason="internal_verified")
        _audit(db_session, "search_external.discovered", reason="external_discovered:2")
        m = external_search_metrics(db_session)
        assert m["search_route_internal_only"] == 1
        assert m["search_route_external_fallback"] == 1
        assert m["internal_zero_result_rate"] == 0.5
        assert m["external_candidates_found"] == 2

    def test_outreach_and_conversion_metrics(self, db_session):
        opp = _seed_opportunity(db_session, status="PROVIDER_ACCEPTED")
        _seed_outreach(db_session, opp, response="ACCEPTED", status="SENT")
        _audit(db_session, "search_external.discovered", reason="external_discovered:1")
        _audit(db_session, "outreach.created")

        m = external_search_metrics(db_session)
        assert m["provider_outreach_delivered"] >= 1
        assert m["provider_acceptance_rate"] == 1.0
        assert m["request_contact_rate"] == 1.0
        assert m["claim_and_list_conversion"] == 0.0  # engaged but nothing internalized yet

    def test_internalized_opportunity_counts_converted(self, db_session):
        _seed_opportunity(db_session, status="INTERNALIZED_VERIFIED", verification="VERIFIED_AUTHORITY")
        _seed_opportunity(db_session, status="PROVIDER_ACCEPTED", verification="VERIFICATION_IN_PROGRESS", idx=2)
        m = external_search_metrics(db_session)
        assert m["claim_and_list_conversion"] == 0.5
        assert m["verification_completion_rate"] == 0.5

    def test_negative_signals_counted(self, db_session):
        _audit(db_session, "circumvention.attempt")
        _audit(db_session, "source.takedown")
        _audit(db_session, "external.stale_reported")
        m = external_search_metrics(db_session)
        assert m["circumvention_attempts"] == 1
        assert m["source_takedown_or_complaints"] == 1
        assert m["external_stale_or_inaccurate_reports"] == 1
        assert m["external_stale_or_inaccurate_report_rate"] is None  # no searches recorded