"""ZR-ENG-CLR-012 Section 31: Observability, SLAs and Operational Controls.
Same posture as services/operational_metrics.py (ZR-ENG-CLR-001 Section
15): no metrics/observability infrastructure (Prometheus, StatsD, etc.)
exists anywhere in this codebase, so these are computed on demand from
existing tables via one read-only admin endpoint rather than pushed
anywhere."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.break_glass_access import BreakGlassAccessGrant
from app.models.evidence_artifact import EvidenceArtifact
from app.models.occupancy_eligibility_check import OCCUPANCY_ELIGIBILITY_TERMINAL_STATUSES, OccupancyEligibilityCheck
from app.models.property_compliance_credential import PropertyComplianceCredential
from app.models.screening_check import SCREENING_CHECK_TERMINAL_STATUSES, ScreeningCheck


def _avg_turnaround_seconds(rows: list[tuple[datetime, datetime]]) -> float | None:
    if not rows:
        return None
    deltas = [(checked_at - created_at).total_seconds() for created_at, checked_at in rows]
    return round(sum(deltas) / len(deltas), 2)


def compute_verification_operational_metrics(db: Session) -> dict:
    now = datetime.now(timezone.utc)

    occupancy_pending = db.scalar(
        select(func.count(OccupancyEligibilityCheck.id)).where(
            OccupancyEligibilityCheck.status.notin_(OCCUPANCY_ELIGIBILITY_TERMINAL_STATUSES)
        )
    )
    occupancy_turnaround_rows = db.execute(
        select(OccupancyEligibilityCheck.created_at, OccupancyEligibilityCheck.checked_at)
        .where(OccupancyEligibilityCheck.checked_at.is_not(None))
    ).all()

    screening_pending = db.scalar(
        select(func.count(ScreeningCheck.id)).where(ScreeningCheck.decision_status.notin_(SCREENING_CHECK_TERMINAL_STATUSES))
    )
    screening_turnaround_rows = db.execute(
        select(ScreeningCheck.created_at, ScreeningCheck.reviewed_at).where(ScreeningCheck.reviewed_at.is_not(None))
    ).all()

    property_credentials_expiring_soon = db.scalar(
        select(func.count(PropertyComplianceCredential.id)).where(
            PropertyComplianceCredential.status == "VALID",
            PropertyComplianceCredential.expires_at.is_not(None),
            PropertyComplianceCredential.expires_at <= now + timedelta(days=30),
        )
    )

    evidence_pending_deletion = db.scalar(
        select(func.count(EvidenceArtifact.id)).where(EvidenceArtifact.deleted_at.is_(None))
    )

    break_glass_grants_last_30_days = db.scalar(
        select(func.count(BreakGlassAccessGrant.id)).where(BreakGlassAccessGrant.granted_at >= now - timedelta(days=30))
    )

    return {
        "occupancy_eligibility_pending_count": occupancy_pending or 0,
        "occupancy_eligibility_avg_turnaround_seconds": _avg_turnaround_seconds(occupancy_turnaround_rows),
        "screening_pending_count": screening_pending or 0,
        "screening_avg_turnaround_seconds": _avg_turnaround_seconds(screening_turnaround_rows),
        "property_credentials_expiring_within_30_days": property_credentials_expiring_soon or 0,
        "evidence_artifacts_not_yet_swept_for_retention": evidence_pending_deletion or 0,
        "break_glass_grants_last_30_days": break_glass_grants_last_30_days or 0,
    }
