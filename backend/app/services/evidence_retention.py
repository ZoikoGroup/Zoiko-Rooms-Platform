"""ZR-ENG-CLR-012 Section 28/AC-28/AC-29: "Documents are never stored
permanently by default. Every artifact class has an effective-dated
retention profile... Scheduled deletion is auditable."

No job scheduler exists in this stack (see services/booking_expiry.py's own
docstring on the same limitation). Consistent with that existing pattern,
this is an on-demand bulk sweep for admin/ops use until a real scheduler
exists. Deletes only the underlying file bytes -- the EvidenceArtifact row
itself (and its deleted_at timestamp) is kept as the permanent audit record
that a deletion happened, and the decision/credential it backed
(VerificationCredential) is retained separately and indefinitely, per the
doc's own distinction between raw evidence and the credential it produced."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dispute_evidence_uploads import delete_dispute_evidence_file
from app.core.identity_uploads import resolve_identity_document_path
from app.crud.market_policy import jurisdiction_code_for_occupancy, resolve_market_policy
from app.models.dispute import DisputeResolutionCase
from app.models.dispute_evidence import DisputeEvidenceItem
from app.models.evidence_artifact import EvidenceArtifact
from app.models.identity_verification import IdentityVerification
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property


def _retention_days_for_artifact(db: Session, artifact: EvidenceArtifact) -> int | None:
    """Only identity_verification artifacts are wired to a real retention
    source today (MarketPolicyPack.identity_evidence_retention_days,
    resolved by the evidence subject's own party jurisdiction) -- other
    related_entity_types have no evidence file of their own yet (occupancy
    eligibility/screening/property compliance don't accept a file upload
    in this MVP), so there is nothing to resolve retention for."""
    if artifact.related_entity_type != "identity_verification":
        return None
    record = db.get(IdentityVerification, int(artifact.related_entity_id))
    if record is None:
        return None
    party = db.get(Party, record.party_id)
    if party is None:
        return None
    try:
        policy = resolve_market_policy(db, party.jurisdiction)
    except HTTPException:
        # No pack configured for this party's jurisdiction -- same
        # fail-open-on-retention posture as every other resolver here:
        # nothing to delete against, not an error worth crashing the sweep.
        return None
    return policy.identity_evidence_retention_days


def sweep_expired_evidence(db: Session, *, now: datetime | None = None) -> list[EvidenceArtifact]:
    """Deletes the on-disk file for every not-yet-deleted artifact whose
    jurisdiction-configured retention window has passed. Idempotent and
    safe to run repeatedly -- an artifact with no file left on disk (e.g.
    a previous partial run) is still marked deleted_at rather than erroring."""
    now = now or datetime.now(timezone.utc)
    candidates = list(db.scalars(select(EvidenceArtifact).where(EvidenceArtifact.deleted_at.is_(None))))

    deleted: list[EvidenceArtifact] = []
    for artifact in candidates:
        retention_days = _retention_days_for_artifact(db, artifact)
        if retention_days is None:
            continue
        cutoff = artifact.created_at + timedelta(days=retention_days)
        if now < cutoff:
            continue

        path = resolve_identity_document_path(artifact.stored_filename)
        if path.is_file():
            path.unlink()
        artifact.deleted_at = now
        deleted.append(artifact)

    if deleted:
        db.commit()
    return deleted


def _jurisdiction_code_for_case(db: Session, case: DisputeResolutionCase) -> str | None:
    """Resolves whichever of the case's two possible links (an occupancy,
    or a bare property for a pre-occupancy dispute) can reach a
    jurisdiction -- mirrors DisputeResolutionCase.occupancy_id/property_id's
    own "not every claim family has both" nullability."""
    if case.occupancy_id is not None:
        occupancy = db.get(Occupancy, case.occupancy_id)
        if occupancy is not None:
            return jurisdiction_code_for_occupancy(occupancy)
    if case.property_id is not None:
        prop = db.get(Property, case.property_id)
        if prop is not None:
            return prop.jurisdiction_code
    return None


def sweep_expired_dispute_evidence(db: Session, *, now: datetime | None = None) -> list[DisputeEvidenceItem]:
    """Section 12 gap: the counterpart to sweep_expired_evidence above, for
    dispute evidence instead of identity documents. An ACTIVE legal hold
    (models/dispute_legal_hold.py) always blocks deletion here, same as the
    existing manual archive_evidence/request_deletion paths already refuse
    to touch held evidence -- this sweep is additive automation on top of
    that same rule, never a bypass of it."""
    from app.crud.dispute_evidence import _active_legal_hold

    now = now or datetime.now(timezone.utc)
    candidates = list(
        db.scalars(
            select(DisputeEvidenceItem).where(
                DisputeEvidenceItem.deleted_at.is_(None), DisputeEvidenceItem.stored_filename.is_not(None),
            )
        )
    )

    deleted: list[DisputeEvidenceItem] = []
    for item in candidates:
        if item.legal_hold or _active_legal_hold(db, item) is not None:
            continue
        case = db.get(DisputeResolutionCase, item.case_id)
        if case is None:
            continue
        jurisdiction_code = _jurisdiction_code_for_case(db, case)
        if jurisdiction_code is None:
            continue
        try:
            policy = resolve_market_policy(db, jurisdiction_code)
        except HTTPException:
            continue
        retention_days = policy.dispute_evidence_retention_days
        if retention_days is None:
            continue
        cutoff = item.created_at + timedelta(days=retention_days)
        if now < cutoff:
            continue

        delete_dispute_evidence_file(item.stored_filename)
        item.stored_filename = None
        item.original_filename = ""
        item.deleted_at = now
        deleted.append(item)

    if deleted:
        db.commit()
    return deleted
