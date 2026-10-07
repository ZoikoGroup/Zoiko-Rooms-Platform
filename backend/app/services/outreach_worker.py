"""Background processor for pending provider outreach requests
(ZR-AI-SEARCH-001 Phase 1.3, Section 9).

There is no outbound channel adapter in the core codebase, so dispatch is
injectable: a deployment provides `dispatch(db, outreach, body) -> message_id`
(an email/sms relay). Without one nothing is sent and rows stay PENDING in the
operator (manual outreach) queue.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.external_search import ExternalOpportunity, ProviderOutreach, SourceRightRegistry
from app.services.audit_ext import log_external_search_event

DispatchCallable = Callable[[Session, ProviderOutreach, str], str]


class OutreachWorker:
    DEFAULT_EXPIRY_TTL_DAYS = 14
    # Used when the source has no registry row; also the floor for the query.
    DEFAULT_OPPORTUNITY_TTL_SECONDS = 24 * 3600
    MIN_OPPORTUNITY_TTL_SECONDS = 60

    def __init__(self) -> None:
        from app.services.external_outreach import outreach_service

        self._service = outreach_service

    def process_pending_outreach(
        self,
        db: Session,
        *,
        dispatch: DispatchCallable | None = None,
        limit: int = 200,
    ) -> int:
        """For each PENDING outreach: re-check eligibility, send via the
        allowed channel, move to SENT/FAILED, and audit."""
        from app.services.external_outreach import OUTREACH_FLAG
        from app.services.feature_flags import is_enabled

        if dispatch is None or not is_enabled(db, OUTREACH_FLAG):
            return 0
        rows = list(
            db.execute(
                select(ProviderOutreach)
                .where(ProviderOutreach.outreach_status == "PENDING")
                .order_by(ProviderOutreach.requested_at.asc())
                .limit(limit)
            ).scalars()
        )
        sent = 0
        for po in rows:
            try:
                # SAVEPOINT per row: a DB error in one dispatch must not leave
                # the session unusable for the remaining rows and the commit.
                with db.begin_nested():
                    dispatched = self._service.send_outreach(
                        db, po, dispatch=dispatch, now=datetime.now(timezone.utc)
                    )
                if dispatched.outreach_status == "SENT":
                    sent += 1
            except Exception:
                po.outreach_status = "FAILED"
                log_external_search_event(
                    db,
                    action="outreach.failed",
                    resource_type="provider_outreach",
                    resource_id=str(po.id),
                    reason="worker dispatch exception",
                )
        db.commit()
        return sent

    def check_expired_outreach(
        self,
        db: Session,
        *,
        ttl_days: int | None = None,
        now: datetime | None = None,
        limit: int = 200,
    ) -> int:
        """Expire SENT outreach with no provider response within the TTL --
        marked EXPIRED with provider_response NO_RESPONSE for the record."""
        now = now or datetime.now(timezone.utc)
        ttl = timedelta(days=ttl_days or self.DEFAULT_EXPIRY_TTL_DAYS)
        cutoff = now - ttl
        rows = list(
            db.execute(
                select(ProviderOutreach)
                .where(
                    ProviderOutreach.outreach_status == "SENT",
                    ProviderOutreach.outreach_sent_at.is_not(None),
                    ProviderOutreach.outreach_sent_at <= cutoff,
                )
                .order_by(ProviderOutreach.outreach_sent_at.asc())
                .limit(limit)
            ).scalars()
        )
        expired = 0
        for po in rows:
            po.outreach_status = "EXPIRED"
            po.provider_response = "NO_RESPONSE"
            po.provider_response_at = now
            self._service._append_trail(  # noqa: SLF001 - worker keeps its state machine in one place
                po,
                "step:expired",
                {"ttl_days": ttl.days, "no_response_within_ttl": True},
            )
            log_external_search_event(
                db,
                action="outreach.expired",
                resource_type="provider_outreach",
                resource_id=str(po.id),
                reason=f"no response within {ttl.days}d",
            )
            expired += 1
        if expired:
            db.commit()
        return expired

    def purge_stale_opportunities(
        self,
        db: Session,
        *,
        now: datetime | None = None,
        limit: int = 1000,
    ) -> int:
        """Section 6.3 / 12 retention: delete discovered external
        opportunities past their source's cache TTL that no renter asked
        Zoiko Rooms to act on, so search results never build a permanent
        shadow database of third-party listings. Opportunities with any
        outreach are kept for provenance/audit."""
        now = now or datetime.now(timezone.utc)
        ttl_by_source = {
            sid: ttl
            for sid, ttl in db.execute(
                select(SourceRightRegistry.source_id, SourceRightRegistry.cache_ttl_seconds)
            )
        }
        has_outreach = select(ProviderOutreach.id).where(
            ProviderOutreach.opportunity_id == ExternalOpportunity.id
        ).exists()
        candidates = db.scalars(
            select(ExternalOpportunity)
            .where(
                ExternalOpportunity.status == "EXTERNAL_DISCOVERED",
                ~has_outreach,
                ExternalOpportunity.discovered_at
                <= now - timedelta(seconds=self.MIN_OPPORTUNITY_TTL_SECONDS),
            )
            .order_by(ExternalOpportunity.discovered_at.asc())
            .limit(limit)
        ).all()
        purged = 0
        for opp in candidates:
            ttl = ttl_by_source.get(opp.source_id) or self.DEFAULT_OPPORTUNITY_TTL_SECONDS
            discovered = opp.discovered_at
            if discovered.tzinfo is None:
                discovered = discovered.replace(tzinfo=timezone.utc)
            if discovered > now - timedelta(seconds=ttl):
                continue
            db.delete(opp)
            purged += 1
        if purged:
            log_external_search_event(
                db,
                action="external_opportunity.purged",
                resource_type="external_opportunity",
                resource_id="batch",
                reason=f"expired past source ttl: {purged}",
            )
            db.commit()
        return purged


outreach_worker = OutreachWorker()