"""ZR-ENG-CLR-010 Section 28: "Operational reporting: case volume, claim
mix, time to triage, time to resolution, reopen rate, settlement rate,
external referral rate, deadline breaches, financial hold age and outcome
distribution."

Same pattern as app/services/operational_metrics.py (ZR-ENG-CLR-001 Section
15): no metrics/observability infrastructure exists anywhere in this
codebase, so every figure here is computed on demand from existing tables
via one read-only admin endpoint, not pushed to an external system.
Rate/average helpers return None (not 0) when there's no data to compute
from -- an empty dispute queue should read as "nothing to report yet", not
a fabricated zero.

Deliberately NOT reported, same "state the gap, don't approximate it"
discipline operational_metrics.py already uses for search/index drift:

- Deadline breaches -- no deadline/SLA engine exists in this codebase yet
  (still a deferred Section 10 phase); there is nothing to check a
  case/claim against.
- Time to triage -- crud/disputes.py:open_case resolves the forum and sets
  the case's initial status (TRIAGED or LEGAL_REVIEW_REQUIRED) in the same
  synchronous call that creates the case. There is no later "triage
  happened" moment to measure; reporting ~0 here would describe this MVP's
  lack of an async triage queue, not a real operational fact.
- Fairness monitoring / root-cause reporting -- Section 28 itself gates
  this on "legally permissible operational dimensions" and warns against
  "prohibited discriminatory scoring". Building it without legal review is
  exactly the risk the spec calls out, so it is left alone entirely."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.dispute import DisputeResolutionCase, DisputeResolutionClaim, DisputeResolutionHold
from app.models.dispute_external_proceeding import DisputeExternalProceeding
from app.models.dispute_settlement import DisputeSettlement
from app.schemas.analytics import DisputeOperationalMetricsRead


def _group_counts(db: Session, column) -> dict[str, int]:
    rows = db.execute(select(column, func.count()).group_by(column)).all()
    return {key: count for key, count in rows if key is not None}


def _cases_by_status(db: Session) -> dict[str, int]:
    return _group_counts(db, DisputeResolutionCase.status)


def _cases_by_severity(db: Session) -> dict[str, int]:
    return _group_counts(db, DisputeResolutionCase.severity)


def _claims_by_family(db: Session) -> dict[str, int]:
    return _group_counts(db, DisputeResolutionClaim.claim_family)


def _claims_by_authority_class(db: Session) -> dict[str, int]:
    return _group_counts(db, DisputeResolutionClaim.authority_class)


def _claims_by_outcome(db: Session) -> dict[str, int]:
    return _group_counts(db, DisputeResolutionClaim.outcome)


def _avg_resolution_time_days(db: Session) -> float | None:
    # Deltas computed in Python from the raw columns -- same reasoning as
    # operational_metrics.py's _avg_approval_turnaround_seconds -- rather
    # than a DB-arithmetic subtraction of two DateTime columns, which
    # SQLite (the test harness) and Postgres don't handle the same way.
    rows = db.execute(
        select(DisputeResolutionCase.opened_at, DisputeResolutionCase.closed_at).where(
            DisputeResolutionCase.closed_at.is_not(None)
        )
    ).all()
    if not rows:
        return None
    deltas = [(closed_at - opened_at).total_seconds() for opened_at, closed_at in rows]
    return round(sum(deltas) / len(deltas) / 86400, 2)


def _reopen_rate(db: Session, total_cases: int) -> float | None:
    if total_cases == 0:
        return None
    reopened = db.scalar(
        select(func.count()).select_from(DisputeResolutionCase).where(DisputeResolutionCase.reopened_at.is_not(None))
    ) or 0
    return round(reopened / total_cases, 4)


def _settlement_rate(db: Session) -> float | None:
    """Of cases where a settlement was ever proposed, what fraction actually
    reached EFFECTIVE -- 'how many attempted negotiations succeeded', not
    'what fraction of all cases settled' (most cases never attempt one)."""
    cases_with_any_settlement = db.scalar(
        select(func.count(func.distinct(DisputeSettlement.case_id)))
    ) or 0
    if cases_with_any_settlement == 0:
        return None
    cases_with_effective_settlement = db.scalar(
        select(func.count(func.distinct(DisputeSettlement.case_id))).where(DisputeSettlement.status == "EFFECTIVE")
    ) or 0
    return round(cases_with_effective_settlement / cases_with_any_settlement, 4)


def _external_referral_rate(db: Session, total_cases: int) -> float | None:
    if total_cases == 0:
        return None
    cases_with_proceeding = db.scalar(
        select(func.count(func.distinct(DisputeExternalProceeding.case_id)))
    ) or 0
    return round(cases_with_proceeding / total_cases, 4)


def _avg_active_financial_hold_age_days(db: Session) -> float | None:
    # Computed in Python, not SQL -- same reasoning as
    # operational_metrics.py's own delta helpers: pull the raw timestamps
    # and subtract in Python rather than relying on a DB-specific interval
    # expression (func.now() arithmetic behaves inconsistently between the
    # SQLite test harness and real Postgres).
    created_at_values = db.scalars(
        select(DisputeResolutionHold.created_at).where(DisputeResolutionHold.status == "ACTIVE")
    ).all()
    if not created_at_values:
        return None
    now = datetime.now(timezone.utc)
    return round(sum((now - created_at).total_seconds() for created_at in created_at_values) / len(created_at_values) / 86400, 2)


def _avg_released_financial_hold_lifetime_days(db: Session) -> float | None:
    rows = db.execute(
        select(DisputeResolutionHold.created_at, DisputeResolutionHold.released_at).where(
            DisputeResolutionHold.status == "RELEASED", DisputeResolutionHold.released_at.is_not(None)
        )
    ).all()
    if not rows:
        return None
    deltas = [(released_at - created_at).total_seconds() for created_at, released_at in rows]
    return round(sum(deltas) / len(deltas) / 86400, 2)


def compute_dispute_operational_metrics(db: Session) -> DisputeOperationalMetricsRead:
    total_cases = db.scalar(select(func.count(DisputeResolutionCase.id))) or 0
    return DisputeOperationalMetricsRead(
        total_cases=total_cases,
        cases_by_status=_cases_by_status(db),
        cases_by_severity=_cases_by_severity(db),
        claims_by_family=_claims_by_family(db),
        claims_by_authority_class=_claims_by_authority_class(db),
        claims_by_outcome=_claims_by_outcome(db),
        avg_resolution_time_days=_avg_resolution_time_days(db),
        reopen_rate=_reopen_rate(db, total_cases),
        settlement_rate=_settlement_rate(db),
        external_referral_rate=_external_referral_rate(db, total_cases),
        avg_active_financial_hold_age_days=_avg_active_financial_hold_age_days(db),
        avg_released_financial_hold_lifetime_days=_avg_released_financial_hold_lifetime_days(db),
    )
