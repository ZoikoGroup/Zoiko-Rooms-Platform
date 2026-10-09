"""ZR-AI-SEARCH-001 Section 16 -- audit, observability and commercial metrics.

Metrics are computed on demand from the audit chain plus the opportunity /
outreach / report tables through the super-admin endpoint. Rates return None
on a zero denominator rather than a fabricated 0%.
"""

from __future__ import annotations

from app.crud.audit import log_audit_event
from app.models.external_search import ExternalOpportunity, ExternalOpportunityReport, ProviderOutreach
from app.services.external_search_metrics import external_search_metrics

from tests.conftest import _make_user


def _seed_opportunity(db, *, status="EXTERNAL_DISCOVERED", verification="NOT_VERIFIED_BY_ZOIKO_ROOMS", idx=1,
                      internal_listing_id=None):
    opp = ExternalOpportunity(
        external_opportunity_id=f"ext-m{idx}",
        source_id="s1",
        status=status,
        verification_status=verification,
        approx_location="Bristol city centre area",
        internal_listing_id=internal_listing_id,
    )
    db.add(opp)
    db.flush()
    return opp


def _seed_outreach(db, opp, *, response=None, status="SENT"):
    user = _make_user(db, email=f"renter-{opp.id}@test.com")
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


def _audit(db, action, reason="", resource_id="q_1"):
    log_audit_event(db, actor=None, action=action, resource_type="search", resource_id=resource_id,
                    correlation_id="c-metrics", reason=reason)


class TestSection16Metrics:
    def test_empty_db_returns_none_rates_not_zeros(self, db_session):
        m = external_search_metrics(db_session, days=30)
        assert m["search_route_internal_only"] == 0
        assert m["internal_zero_result_rate"] is None
        assert m["provider_acceptance_rate"] is None
        assert m["claim_and_list_conversion"] is None
        assert m["lead_to_tenancy_rate"] is None

    def test_routes_and_zero_result_rate_by_market(self, db_session):
        _audit(db_session, "search_external.waterfall", "internal_only:3;disclosure=x;actor=system;market=GB")
        _audit(db_session, "search_external.discovered", "external_discovered:2;disclosure=x;actor=system;market=GB")
        _audit(db_session, "search_external.not_activated", "internal_zero;actor=visitor;market=IN")
        _audit(db_session, "search_external.fair_housing_blocked", "protected_class:x;actor=system;market=GB")
        m = external_search_metrics(db_session)
        assert m["search_route_internal_only"] == 1
        assert m["search_route_external_fallback"] == 1
        assert m["search_external_not_activated"] == 1
        assert m["search_blocked_prohibited_criteria"] == 1
        assert m["internal_zero_result_rate"] == round(2 / 3, 4)
        assert m["internal_zero_result_rate_by_market"]["GB"]["zero_result_rate"] == 0.5
        assert m["internal_zero_result_rate_by_market"]["IN"]["zero_result_rate"] == 1.0
        assert m["external_candidates_displayable"] == 2

    def test_candidates_found_vs_displayable(self, db_session):
        _audit(db_session, "search_external.provider_results", "candidates=5")
        _audit(db_session, "search_external.web_results", "hits=3 sites=4")
        _audit(db_session, "search_external.discovered", "external_discovered:6;market=GB")
        _audit(db_session, "search_external.registered_internally", "registered_internally=2")
        m = external_search_metrics(db_session)
        assert m["external_candidates_found"] == 8
        assert m["external_candidates_displayable"] == 6
        assert m["external_candidates_registered_internally"] == 2

    def test_outreach_and_acceptance(self, db_session):
        opp = _seed_opportunity(db_session, status="PROVIDER_ACCEPTED")
        _seed_outreach(db_session, opp, response="ACCEPTED", status="SENT")
        _audit(db_session, "search_external.discovered", "external_discovered:1;market=GB")
        m = external_search_metrics(db_session)
        assert m["provider_outreach_delivered"] == 1
        assert m["provider_acceptance_rate"] == 1.0
        assert m["request_contact_rate"] == 1.0
        assert m["claim_and_list_conversion"] == 0.0  # accepted, not yet internalised

    def test_claim_conversion_and_verification(self, db_session):
        from tests.test_search_orchestrator import _seed_listing

        done = _seed_listing(db_session, slug="claimed-done")
        started = _seed_listing(db_session, slug="claimed-started")
        a = _seed_opportunity(db_session, status="INTERNALIZED_VERIFIED", verification="VERIFIED_AUTHORITY",
                              internal_listing_id=done.id)
        b = _seed_opportunity(db_session, status="VERIFICATION_IN_PROGRESS", idx=2, internal_listing_id=started.id)
        _seed_outreach(db_session, a, response="ACCEPTED")
        _seed_outreach(db_session, b, response="ACCEPTED")
        m = external_search_metrics(db_session)
        assert m["claims_started"] == 2
        assert m["claim_and_list_conversion"] == 0.5
        assert m["verification_completion_rate"] == 0.5
        assert m["lead_to_tenancy_rate"] == 0.0

    def test_negative_signals_counted(self, db_session):
        opp = _seed_opportunity(db_session)
        db_session.add(ExternalOpportunityReport(opportunity_id=opp.id, reason="STALE"))
        _audit(db_session, "security.circumvention_request", "user:1")
        _audit(db_session, "security.circumvention_masked", "user:1;masked=url=1")
        _audit(db_session, "source.takedown", "admin:1")
        db_session.flush()
        m = external_search_metrics(db_session)
        assert m["circumvention_attempts"] == 2
        assert m["source_takedown_or_complaints"] == 1
        assert m["external_stale_or_inaccurate_reports"] == 1
        assert m["external_stale_or_inaccurate_report_rate"] is None  # no cards shown yet
