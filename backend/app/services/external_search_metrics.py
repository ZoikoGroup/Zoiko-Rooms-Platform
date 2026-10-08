"""ZR-AI-SEARCH-001 Section 16: audit, observability and commercial metrics.

Same on-demand posture as every other metrics module in this codebase
(services/operational_metrics.py, services/verification_operational_metrics.py):
no push/observability infrastructure exists, so these are computed from the
audit chain (app.models.audit.AuditEvent) plus the ExternalOpportunity /
ProviderOutreach / report tables through one read-only super-admin endpoint.

Search audit rows carry ``...;actor=...;market=XX`` in their reason (see
SearchOrchestrator.search), which gives the per-market breakdown. Rates return
None when the denominator is zero (never a fabricated 0%).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.external_search import (
    ExternalOpportunity,
    ExternalOpportunityReport,
    ProviderOutreach,
    ProviderSuppression,
)

INTERNAL_ONLY = ("search_external.waterfall",)
EXTERNAL_RAN = ("search_external.discovered", "search_external.blocked")
NOT_ACTIVATED = ("search_external.not_activated",)
PROHIBITED = ("search_external.fair_housing_blocked",)
_MARKET = re.compile(r"market=([A-Z]{2}|-)")
_NUM = re.compile(r"(?:candidates|hits|external_discovered|registered_internally)[=:](\d+)")


def _count_actions(db: Session, actions: tuple[str, ...], since: datetime) -> int:
    return db.scalar(
        select(func.count(AuditEvent.id)).where(AuditEvent.action.in_(actions), AuditEvent.created_at >= since)
    ) or 0


def _reasons(db: Session, actions: tuple[str, ...], since: datetime) -> list[tuple[str, str]]:
    return [
        (a, r or "")
        for a, r in db.execute(
            select(AuditEvent.action, AuditEvent.reason).where(
                AuditEvent.action.in_(actions), AuditEvent.created_at >= since
            )
        ).all()
    ]


def _sum_numbers(rows: list[tuple[str, str]]) -> int:
    total = 0
    for _, reason in rows:
        m = _NUM.search(reason)
        total += int(m.group(1)) if m else 0
    return total


def _rate(denominator: int, numerator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def external_search_metrics(db: Session, *, days: int = 30) -> dict[str, Any]:
    """Section 16 metric set over the trailing ``days`` window (default 30)."""
    window = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=window)

    # -- search routes, overall and per market ----------------------------------
    route_rows = _reasons(db, INTERNAL_ONLY + EXTERNAL_RAN + NOT_ACTIVATED + PROHIBITED, since)
    by_market: dict[str, dict[str, int]] = defaultdict(lambda: {"internal_only": 0, "internal_zero": 0, "external_fallback": 0})
    internal_only = external_fallback = not_activated = prohibited = 0
    for action, reason in route_rows:
        market = (_MARKET.search(reason) or [None, "-"])[1]
        if action in INTERNAL_ONLY:
            internal_only += 1
            by_market[market]["internal_only"] += 1
        elif action in EXTERNAL_RAN:
            external_fallback += 1
            by_market[market]["internal_zero"] += 1
            by_market[market]["external_fallback"] += 1
        elif action in NOT_ACTIVATED:
            not_activated += 1
            by_market[market]["internal_zero"] += 1
        else:
            prohibited += 1
    searches = internal_only + external_fallback + not_activated
    zero_results = external_fallback + not_activated

    # -- external candidates --------------------------------------------------------
    found = _sum_numbers(_reasons(db, ("search_external.provider_results", "search_external.web_results"), since))
    displayable = _sum_numbers(_reasons(db, ("search_external.discovered",), since))
    registered_internally = _sum_numbers(_reasons(db, ("search_external.registered_internally",), since))
    rights_blocked = _count_actions(db, ("broker.fetch_blocked", "outreach.blocked.rights", "outreach.blocked.opportunity"), since)

    # -- outreach lifecycle ------------------------------------------------------------
    state_counts = dict(db.execute(
        select(ProviderOutreach.outreach_status, func.count(ProviderOutreach.id))
        .where(ProviderOutreach.created_at >= since).group_by(ProviderOutreach.outreach_status)
    ).all())
    responses = dict(db.execute(
        select(ProviderOutreach.provider_response, func.count(ProviderOutreach.id))
        .where(ProviderOutreach.created_at >= since, ProviderOutreach.provider_response.is_not(None))
        .group_by(ProviderOutreach.provider_response)
    ).all())
    requests = sum(state_counts.values())
    accepted = responses.get("ACCEPTED", 0)
    responded = accepted + responses.get("DECLINED", 0)

    # -- conversion ---------------------------------------------------------------------
    accepted_leads = db.scalar(select(func.count(func.distinct(ProviderOutreach.opportunity_id))).where(
        ProviderOutreach.provider_response == "ACCEPTED", ProviderOutreach.created_at >= since)) or 0
    claimed = db.scalar(select(func.count(ExternalOpportunity.id)).where(
        ExternalOpportunity.internal_listing_id.is_not(None), ExternalOpportunity.created_at >= since)) or 0
    internalised = db.scalar(select(func.count(ExternalOpportunity.id)).where(
        ExternalOpportunity.status == "INTERNALIZED_VERIFIED", ExternalOpportunity.created_at >= since)) or 0
    from app.models.occupancy import Occupancy

    with_tenancy = db.scalar(
        select(func.count(func.distinct(ExternalOpportunity.id)))
        .join(Occupancy, Occupancy.listing_id == ExternalOpportunity.internal_listing_id)
        .where(ExternalOpportunity.created_at >= since, Occupancy.status.in_(("ACTIVE", "ENDED", "PENDING_MOVE_IN")))
    ) or 0

    # -- quality, rights and leakage signals --------------------------------------------------
    reports = db.scalar(select(func.count(ExternalOpportunityReport.id)).where(
        ExternalOpportunityReport.created_at >= since)) or 0
    takedowns = _count_actions(db, ("source.takedown",), since)
    circumvention = _count_actions(db, ("security.circumvention_masked", "security.circumvention_request"), since)
    opted_out = db.scalar(select(func.count(ProviderSuppression.id)).where(ProviderSuppression.created_at >= since)) or 0

    return {
        "window_days": window,
        "search_route_internal_only": internal_only,
        "search_route_external_fallback": external_fallback,
        "search_external_not_activated": not_activated,
        "search_blocked_prohibited_criteria": prohibited,
        "internal_zero_result_rate": _rate(searches, zero_results),
        "internal_zero_result_rate_by_market": {
            m: {**c, "zero_result_rate": _rate(c["internal_only"] + c["internal_zero"], c["internal_zero"])}
            for m, c in sorted(by_market.items())
        },
        "external_candidates_found": found,
        "external_candidates_displayable": displayable,
        "external_candidates_registered_internally": registered_internally,
        "external_candidates_blocked": rights_blocked,
        "request_contact_rate": _rate(displayable, requests),
        "provider_outreach_delivered": state_counts.get("SENT", 0) + state_counts.get("DELIVERED", 0),
        "provider_outreach_pending": state_counts.get("PENDING", 0),
        "provider_outreach_failed": state_counts.get("FAILED", 0),
        "provider_outreach_suppressed": state_counts.get("SUPPRESSED", 0),
        "provider_opt_outs": opted_out,
        "provider_acceptance_rate": _rate(responded, accepted),
        "claim_and_list_conversion": _rate(accepted_leads, internalised),
        "claims_started": claimed,
        "verification_completion_rate": _rate(claimed, internalised),
        "lead_to_tenancy_rate": _rate(internalised, with_tenancy),
        "external_stale_or_inaccurate_reports": reports,
        "external_stale_or_inaccurate_report_rate": _rate(displayable, reports),
        "source_takedown_or_complaints": takedowns,
        "circumvention_attempts": circumvention,
        "total_outreach_rows": requests,
    }
