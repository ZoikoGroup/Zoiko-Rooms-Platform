"""ZR-ENG-CLR-001 Section 15: 'Metrics: approval turnaround; publication
failure rate; availability conflict rate; hold conversion rate; hold expiry
rate; duplicate-confirmation incidents; stale-hold count; search/index
drift; manual override frequency.'

No metrics/observability infrastructure (Prometheus, StatsD, etc.) exists
anywhere in this codebase. Consistent with this stack's established pattern
for anything that would otherwise need a scheduler or push pipeline
(check_alerts.py, reconcile_expirations.py, crud/analytics.py's own
dashboard queries), these are computed on demand from existing tables via
one read-only admin endpoint rather than pushed to an external system.

search/index drift is not reported here: confirmed via a full-repo search
that no search index component exists in this codebase at all, so there is
nothing to measure drift against (AC-10 is satisfied structurally instead --
authoritative confirmation always rechecks inventory directly, never a
cache/index).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.domain_event import DomainEvent
from app.models.leasing import Offer
from app.models.listing_approval import ListingApproval
from app.models.listing_version import ListingVersion
from app.models.room_hold import RoomHold


def _avg_approval_turnaround_seconds(db: Session) -> float | None:
    """Approval turnaround: decided_at - the version's own submitted_at, for
    every APPROVED decision that has both timestamps (a version that was
    never actually submitted -- e.g. auto-promoted non-material edits --
    has no meaningful turnaround to measure)."""
    rows = db.execute(
        select(ListingApproval.decided_at, ListingVersion.submitted_at)
        .join(ListingVersion, ListingVersion.id == ListingApproval.listing_version_id)
        .where(ListingApproval.decision == "APPROVED", ListingVersion.submitted_at.is_not(None))
    ).all()
    if not rows:
        return None
    deltas = [(decided_at - submitted_at).total_seconds() for decided_at, submitted_at in rows]
    return round(sum(deltas) / len(deltas), 2)


def _publication_failure_rate(db: Session) -> float | None:
    """REJECTED decisions as a fraction of every APPROVED-or-REJECTED
    decision (CHANGES_REQUESTED/QUARANTINED/SUSPENDED/OVERRIDE are not a
    binary approve/reject outcome, so they're excluded from this ratio)."""
    counts = dict(
        db.execute(
            select(ListingApproval.decision, func.count(ListingApproval.id))
            .where(ListingApproval.decision.in_(("APPROVED", "REJECTED")))
            .group_by(ListingApproval.decision)
        ).all()
    )
    total = counts.get("APPROVED", 0) + counts.get("REJECTED", 0)
    if total == 0:
        return None
    return round(counts.get("REJECTED", 0) / total, 4)


def _hold_conversion_rate(db: Session) -> float | None:
    """Fraction of every RoomHold ever created that made it to BOOKED
    (a room booking actually completing) rather than being released without
    ever converting (declined/withdrawn/expired)."""
    total = db.scalar(select(func.count(RoomHold.id))) or 0
    if total == 0:
        return None
    booked = db.scalar(select(func.count(RoomHold.id)).where(RoomHold.status == "BOOKED")) or 0
    return round(booked / total, 4)


def _hold_expiry_rate(db: Session) -> float | None:
    """Fraction of every RoomHold ever created that was released specifically
    because its acceptance window expired (Rule 7), as opposed to a normal
    decline/withdraw/booked outcome."""
    total = db.scalar(select(func.count(RoomHold.id))) or 0
    if total == 0:
        return None
    expired = db.scalar(
        select(func.count(RoomHold.id)).where(RoomHold.release_reason == "acceptance_window_expired")
    ) or 0
    return round(expired / total, 4)


def _stale_hold_count(db: Session, *, now: datetime | None = None) -> int:
    """A hold still sitting HELD whose owning Offer's confirmation_expires_at
    has already passed -- i.e. it *should* have been lazily expired or swept
    by reconcile_expirations.py by now but hasn't been yet. Non-zero means
    the reconciliation script isn't running often enough (or at all)."""
    now = now or datetime.now(timezone.utc)
    return db.scalar(
        select(func.count(RoomHold.id))
        .join(Offer, (Offer.id == RoomHold.source_id) & (RoomHold.source_type == "offer"))
        .where(
            RoomHold.released_at.is_(None),
            RoomHold.status == "HELD",
            Offer.status == "ACCEPTED",
            Offer.confirmation_expires_at.is_not(None),
            Offer.confirmation_expires_at <= now,
        )
    ) or 0


def _duplicate_confirmation_incidents(db: Session) -> int:
    """AC-09: 'Repeated payment callback/event does not duplicate
    confirmation.' Counts any idempotency_key that somehow ended up on more
    than one DomainEvent row -- structurally always 0 given the partial
    unique index on domain_events.idempotency_key (see crud/events.py); this
    exists as a live assertion of that guarantee, not a guess."""
    dup_keys = db.execute(
        select(DomainEvent.idempotency_key)
        .where(DomainEvent.idempotency_key.is_not(None))
        .group_by(DomainEvent.idempotency_key)
        .having(func.count(DomainEvent.id) > 1)
    ).all()
    return len(dup_keys)


def _manual_override_frequency(db: Session) -> int:
    """Section 15 'manual override frequency': counts every actually-invoked
    override mechanism in this codebase -- a Super Admin ListingApproval
    override (is_override=True; not yet exercised by any endpoint, always 0
    today) plus every occupant-overlap BLOCK accepted with an explicit
    override reason (Rule 6/Section 9's 'require exception' path, see
    services/overlap.py + crud/leasing.py:_accept_offer_and_hold_room)."""
    listing_overrides = db.scalar(
        select(func.count(ListingApproval.id)).where(ListingApproval.is_override.is_(True))
    ) or 0
    overlap_overrides = db.scalar(
        select(func.count(Offer.id)).where(
            Offer.occupant_risk_tier == "BLOCK", Offer.occupant_risk_reason.ilike("%override%"),
        )
    ) or 0
    return listing_overrides + overlap_overrides


def compute_section1_metrics(db: Session) -> dict:
    return {
        "approval_turnaround_seconds_avg": _avg_approval_turnaround_seconds(db),
        "publication_failure_rate": _publication_failure_rate(db),
        "hold_conversion_rate": _hold_conversion_rate(db),
        "hold_expiry_rate": _hold_expiry_rate(db),
        "stale_hold_count": _stale_hold_count(db),
        "duplicate_confirmation_incidents": _duplicate_confirmation_incidents(db),
        "manual_override_frequency": _manual_override_frequency(db),
    }
