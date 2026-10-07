"""ZR-AI-SEARCH-001 Section 16: audit, observability and commercial metrics.

Same on-demand posture as every other metrics module in this codebase
(services/operational_metrics.py, services/verification_operational_metrics.py):
no push/observability infrastructure exists, so these are computed from the
audit chain (app.models.audit.AuditEvent) plus the ExternalOpportunity /
ProviderOutreach tables through one read-only super-admin endpoint.

Every metric is deliberately conservative:
* rates return None when the denominator is zero (never a fabricated 0%);
* lead_to_tenancy is not lawfully measurable from existing tables and returns
  None with an explanatory note rather than a guess.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent
from app.models.external_search import ExternalOpportunity, ProviderOutreach


def _count_actions(db: Session, actions: tuple[str, ...], since: datetime | None = None) -> int:
    stmt = select(func.count(AuditEvent.id)).where(AuditEvent.action.in_(actions))
    if since is not None:
        stmt = stmt.where(AuditEvent.created_at >= since)
    return db.scalar(stmt) or 0


def _search_route_counts(db: Session, since: datetime) -> dict[str, int]:
    internal_searches = _count_actions(
        db, ("search_external.waterfall", "broker.fetch_allowed"), since
    )
    # Every external fallback run is recorded as a discovered/blocked event.
    external_searches = _count_actions(
        db,
        (
            "search_external.discovered",
            "search_external.blocked",
            "search_external.fair_housing_blocked",
        ),
        since,
    )
    return {"internal_only": internal_searches, "external_fallback": external_searches}


def _candidates(db: Session, since: datetime) -> dict[str, int]:
    """external_candidates_found / displayable / blocked from the audit trail."""
    found = 0
    rows = db.execute(
        select(AuditEvent.reason).where(
            AuditEvent.action == "search_external.discovered",
            AuditEvent.created_at >= since,
        )
    ).all()
    for (reason,) in rows:
        for part in (reason or "").split():
            if part.startswith("external_discovered:"):
                try:
                    found += int(part.rsplit(":", 1)[1])
                except ValueError:
                    found += 0
    blocked = _count_actions(
        db,
        (
            "search_external.blocked",
            "search_external.fair_housing_blocked",
            "broker.fetch_blocked",
            "outreach.blocked.rights",
            "outreach.blocked.opportunity",
        ),
        since,
    )
    return {"found": found, "displayable": found, "blocked": blocked}


def _rate(query_count_denominator: int, numerator: int) -> float | None:
    if not query_count_denominator:
        return None
    return round(numerator / query_count_denominator, 4)


def external_search_metrics(db: Session, *, days: int = 30) -> dict[str, Any]:
    """Section 16 metric set over the trailing ``days`` window (default 30)."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    route = _search_route_counts(db, since)
    internal_only = route["internal_only"]
    external_fallback = route["external_fallback"]
    candidates = _candidates(db, since)

    # Outreach lifecycle from the ProviderOutreach table (window-scoped).
    total_outreach = db.scalar(
        select(func.count(ProviderOutreach.id)).where(ProviderOutreach.created_at >= since)
    ) or 0
    state_counts = dict(
        db.execute(
            select(ProviderOutreach.outreach_status, func.count(ProviderOutreach.id))
            .where(ProviderOutreach.created_at >= since)
            .group_by(ProviderOutreach.outreach_status)
        ).all()
    )
    deliv = state_counts.get("DELIVERED", 0) + state_counts.get("SENT", 0)
    failed = state_counts.get("FAILED", 0)
    suppressed = state_counts.get("SUPPRESSED", 0)

    accepted = db.scalar(
        select(func.count(ProviderOutreach.id)).where(
            ProviderOutreach.provider_response == "ACCEPTED",
            ProviderOutreach.created_at >= since,
        )
    ) or 0
    responded = db.scalar(
        select(func.count(ProviderOutreach.id)).where(
            ProviderOutreach.provider_response.is_not(None),
            ProviderOutreach.created_at >= since,
        )
    ) or 0

    engaged = db.scalar(
        select(func.count(ExternalOpportunity.id)).where(
            ExternalOpportunity.status.in_(
                ("OUTREACH_PENDING", "PROVIDER_ACCEPTED", "VERIFICATION_IN_PROGRESS", "INTERNALIZED_VERIFIED")
            ),
            ExternalOpportunity.created_at >= since,
        )
    ) or 0
    internalized = db.scalar(
        select(func.count(ExternalOpportunity.id)).where(
            ExternalOpportunity.status == "INTERNALIZED_VERIFIED",
            ExternalOpportunity.created_at >= since,
        )
    ) or 0
    fully_verified = db.scalar(
        select(func.count(ExternalOpportunity.id)).where(
            ExternalOpportunity.verification_status == "VERIFIED_AUTHORITY",
            ExternalOpportunity.created_at >= since,
        )
    ) or 0

    discovered = _count_actions(db, ("search_external.discovered",), since)
    intro_requested = _count_actions(
        db,
        (
            "outreach.created",
            "outreach.intro_requested",
        ),
        since,
    )
    # Negative / risk signals recorded on the audit chain by the protocol.
    stale_reports = _count_actions(
        db,
        (
            "external.stale_reported",
            "external.inaccurate_reported",
        ),
        since,
    )
    source_complaints = _count_actions(
        db,
        ("source.takedown", "source.complaint"),
        since,
    )
    circumvention_attempts = _count_actions(
        db,
        ("circumvention.attempt", "circumvention.attack", "broker.url_blocked"),
        since,
    )

    return {
        "window_days": max(1, min(days, 365)),
        "search_route_internal_only": internal_only,
        "search_route_external_fallback": external_fallback,
        "internal_zero_result_rate": _rate(internal_only + external_fallback, external_fallback),
        "external_candidates_found": candidates["found"],
        "external_candidates_displayable": candidates["displayable"],
        "external_candidates_blocked": candidates["blocked"],
        "request_contact_rate": _rate(discovered, intro_requested),
        "provider_outreach_delivered": deliv,
        "provider_outreach_failed": failed,
        "provider_outreach_suppressed": suppressed,
        "provider_acceptance_rate": _rate(responded, accepted),
        "claim_and_list_conversion": _rate(engaged, internalized),
        "verification_completion_rate": _rate(engaged, fully_verified),
        "lead_to_tenancy_rate": None,
        "lead_to_tenancy_note": (
            "Not lawfully measurable from existing tables: tenancy formation is "
            "recorded outside the protocol's scope; returning None rather than a guess."
        ),
        "external_stale_or_inaccurate_report_rate": _rate(
            internal_only + external_fallback, stale_reports
        ),
        "external_stale_or_inaccurate_reports": stale_reports,
        "source_takedown_or_complaints": source_complaints,
        "circumvention_attempts": circumvention_attempts,
        "total_outreach_rows": total_outreach,
    }