"""ZR-ENG-CLR-010 Section 13 (P4/P5/P6)/24: files and resolves external
proceedings for A2+ (external-only) claims.

NON-BEHAVIOR, same discipline as crud/disputes.py's own AC-9 note: nothing
here ever touches DepositRecord, DepositClaim, Obligation, RefundRequest or
PayoutRecord. Recording a DECIDED deposit-scheme proceeding is a record of
what the scheme decided on this claim, never an instruction to release
money -- Section 14/AC-8 keep Section 2's existing custody/scheme logic
(crud/finance.py) as the sole authority over protected deposit funds. Same
applies to any access/possession consequence of a tribunal order -- this
module records the outcome; it never revokes access or enforces possession
itself (Section 2/18's "no self-help" rule).

QA-Q19: decision-recording dual control is threshold-gated (see
_requires_external_proceeding_dual_control), not unconditional -- same
maker-checker shape as crud/disputes.py's financial-hold gating.

QA-Q21/Q22/Q23: filing also computes an honest statutory-filing-deadline
flag (never invented, never enforced -- see
DisputeExternalProceeding.external_deadline_at's own docstring) and, when
the case's market pack configures dispute_conciliation_requirement as
MANDATORY, gates a non-MEDIATION_ADR filing behind an already-concluded
MEDIATION_ADR proceeding on the same case (see
_assert_conciliation_prerequisite_met) -- OPTIONAL/NOT_REQUIRED (the
default, matching the doc's own "no universal mandatory arbitration" rule)
gates nothing."""

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.disputes import _resolve_market_policy_for_occupancy, _sync_case_status_after_claim_change, record_dispute_decision
from app.models.admin_user import AdminUser
from app.models.dispute import DisputeResolutionCase, DisputeResolutionClaim
from app.models.dispute_external_proceeding import (
    DISPUTE_EXTERNAL_PROCEEDING_FINALITY_STATES,
    EXTERNAL_PROCEEDING_AUTHORITY_TYPES,
    DisputeExternalProceeding,
    DisputeExternalProceedingClaimLink,
)
from app.services.dispute_state_machine import (
    EXTERNAL_PROCEEDING_TERMINAL_STATUSES,
    transition_claim,
    transition_external_proceeding,
)


def _claims_for_ids(db: Session, case: DisputeResolutionCase, claim_ids: list[int]) -> list[DisputeResolutionClaim]:
    if not claim_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An external proceeding must be linked to at least one claim")
    claims: list[DisputeResolutionClaim] = []
    for claim_id in claim_ids:
        claim = db.get(DisputeResolutionClaim, claim_id)
        if not claim or claim.case_id != case.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Claim {claim_id} does not belong to this case")
        # Mirror image of decide_claim's AC-6 guard: A0 (Zoiko-controlled)
        # claims are internal by definition -- they don't get externally filed.
        if claim.authority_class == "A0":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Claim {claim_id} is Zoiko-controlled (A0) and cannot be externally filed")
        claims.append(claim)
    return claims


def _assert_conciliation_prerequisite_met(db: Session, case: DisputeResolutionCase, authority_type: str) -> None:
    """QA-Q22/Q23: "Mandatory conciliation market: external referral is
    gated by required pre-action step" / "Optional mediation market: user
    can decline without losing non-waivable court/tribunal route." A
    MEDIATION_ADR filing is itself the conciliation step, so it's never
    gated against itself. Anything else is only gated when the market
    pack says MANDATORY (never the default NOT_REQUIRED, and never merely
    OPTIONAL -- Q23's own point is that declining an optional step must
    never cost the party their court/tribunal route)."""
    if authority_type == "MEDIATION_ADR" or case.occupancy is None:
        return
    market_policy_pack = _resolve_market_policy_for_occupancy(db, case.occupancy)
    if market_policy_pack is None or market_policy_pack.dispute_conciliation_requirement != "MANDATORY":
        return
    conciliation_proceedings = list(
        db.scalars(
            select(DisputeExternalProceeding).where(
                DisputeExternalProceeding.case_id == case.id, DisputeExternalProceeding.authority_type == "MEDIATION_ADR",
            )
        )
    )
    concluded = any(p.status in EXTERNAL_PROCEEDING_TERMINAL_STATUSES for p in conciliation_proceedings)
    if not concluded:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This market requires conciliation/mediation to be attempted and concluded before any other external referral",
        )


def _compute_external_filing_deadline(
    db: Session, case: DisputeResolutionCase, claims: list[DisputeResolutionClaim], filed_at: date,
) -> tuple[date | None, bool]:
    """QA-Q21: computed only when the market pack actually configures a
    filing-deadline window -- see external_deadline_at's own docstring for
    why an unconfigured market records nothing rather than a guess."""
    if case.occupancy is None:
        return None, False
    market_policy_pack = _resolve_market_policy_for_occupancy(db, case.occupancy)
    if market_policy_pack is None or market_policy_pack.dispute_external_filing_deadline_days is None:
        return None, False
    earliest_claim_created_at = min(claim.created_at for claim in claims)
    deadline = (earliest_claim_created_at + timedelta(days=market_policy_pack.dispute_external_filing_deadline_days)).date()
    return deadline, filed_at > deadline


def file_proceeding(
    db: Session,
    case: DisputeResolutionCase,
    admin: AdminUser,
    *,
    authority_type: str,
    authority_name: str,
    external_reference: str,
    claim_ids: list[int],
    filed_at: date | None = None,
) -> DisputeExternalProceeding:
    if authority_type not in EXTERNAL_PROCEEDING_AUTHORITY_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown authority type '{authority_type}'")
    claims = _claims_for_ids(db, case, claim_ids)
    _assert_conciliation_prerequisite_met(db, case, authority_type)
    resolved_filed_at = filed_at or datetime.now(timezone.utc).date()
    external_deadline_at, filed_after_deadline = _compute_external_filing_deadline(db, case, claims, resolved_filed_at)

    proceeding = DisputeExternalProceeding(
        case_id=case.id,
        authority_type=authority_type,
        authority_name=authority_name,
        external_reference=external_reference,
        external_deadline_at=external_deadline_at,
        filed_after_deadline=filed_after_deadline,
        status="FILED",
        filed_at=filed_at,
        filed_by_admin_id=admin.id,
    )
    db.add(proceeding)
    db.flush()

    for claim in claims:
        db.add(DisputeExternalProceedingClaimLink(proceeding_id=proceeding.id, claim_id=claim.id))
        if claim.status != "EXTERNAL_REFERRAL":
            transition_claim(claim, "EXTERNAL_REFERRAL", note="external proceeding filed")

    _sync_case_status_after_claim_change(case)

    db.commit()
    db.refresh(proceeding)
    return proceeding


def get_proceeding_or_404(db: Session, proceeding_id: int) -> DisputeExternalProceeding:
    proceeding = db.get(DisputeExternalProceeding, proceeding_id)
    if not proceeding:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "External proceeding not found")
    return proceeding


def claim_ids_for_proceeding(db: Session, proceeding: DisputeExternalProceeding) -> list[int]:
    return list(
        db.scalars(
            select(DisputeExternalProceedingClaimLink.claim_id).where(
                DisputeExternalProceedingClaimLink.proceeding_id == proceeding.id
            )
        )
    )


def list_proceedings_for_case(db: Session, case: DisputeResolutionCase) -> list[DisputeExternalProceeding]:
    query = (
        select(DisputeExternalProceeding)
        .where(DisputeExternalProceeding.case_id == case.id)
        .order_by(DisputeExternalProceeding.created_at.desc())
    )
    return list(db.scalars(query))


def update_status(db: Session, proceeding: DisputeExternalProceeding, admin: AdminUser, *, new_status: str) -> DisputeExternalProceeding:
    resolved_outcome = {"DISMISSED": "NOT_UPHELD", "WITHDRAWN": "WITHDRAWN"}.get(new_status)
    if new_status not in ("ACCEPTED", "PENDING", "DISMISSED", "WITHDRAWN"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status must be ACCEPTED, PENDING, DISMISSED or WITHDRAWN")

    transition_external_proceeding(proceeding, new_status)

    if resolved_outcome:
        for claim_id in claim_ids_for_proceeding(db, proceeding):
            claim = db.get(DisputeResolutionClaim, claim_id)
            if claim and claim.status not in ("UPHELD", "PARTLY_UPHELD", "NOT_UPHELD", "SETTLED", "WITHDRAWN"):
                transition_claim(claim, resolved_outcome, note=f"external proceeding {new_status.lower()}")
                claim.outcome = resolved_outcome
                claim.decided_at = datetime.now(timezone.utc)
                claim.decided_by_admin_id = admin.id
                record_dispute_decision(
                    db, claim, outcome=resolved_outcome, basis=f"EXTERNAL_PROCEEDING_{new_status}", authority="external_proceeding",
                    decided_by_admin_id=admin.id, external_proceeding_id=proceeding.id,
                )
        _sync_case_status_after_claim_change(proceeding.case)

    db.commit()
    db.refresh(proceeding)
    return proceeding


def _requires_external_proceeding_dual_control(db: Session, proceeding: DisputeExternalProceeding) -> bool:
    """QA-Q19: Section 20's "mandatory above configured thresholds or for
    safety/legal/manual override cases" maker-checker rule, applied to who
    may record an external proceeding's decision -- not the previous,
    unconditional "always a different admin" behavior. Same three concrete
    triggers as crud/disputes.py's _requires_maker_checker: case severity,
    any linked claim's unresolved-forum flag, or any linked claim's amount."""
    if proceeding.case.severity == "SEV-0":
        return True
    for claim_id in claim_ids_for_proceeding(db, proceeding):
        claim = db.get(DisputeResolutionClaim, claim_id)
        if not claim:
            continue
        if claim.resolver_confidence == "LEGAL_REVIEW_REQUIRED":
            return True
        if claim.amount is not None and claim.amount >= settings.dispute_external_proceeding_dual_control_threshold:
            return True
    return False


def record_decision(
    db: Session,
    proceeding: DisputeExternalProceeding,
    admin: AdminUser,
    *,
    outcome: str,
    decision_date: date,
    finality_state: str,
    outcome_evidence_id: int | None = None,
    reason_code: str = "",
) -> DisputeExternalProceeding:
    # QA-Q19/Section 20: dual control is only mandatory at/above the
    # configured threshold or for a safety/legal-review case -- below every
    # trigger, the filer may also record the decision (mirrors
    # crud/disputes.py's financial-hold maker-checker gating exactly).
    if admin.id == proceeding.filed_by_admin_id and _requires_external_proceeding_dual_control(db, proceeding):
        raise HTTPException(status.HTTP_409_CONFLICT, "The admin who filed this proceeding cannot also record its decision")
    if proceeding.status not in ("FILED", "ACCEPTED", "PENDING"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot record a decision on a proceeding that is already {proceeding.status}")
    if outcome not in ("UPHELD", "PARTLY_UPHELD", "NOT_UPHELD", "SETTLED"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "outcome must be UPHELD, PARTLY_UPHELD, NOT_UPHELD or SETTLED")
    if finality_state not in DISPUTE_EXTERNAL_PROCEEDING_FINALITY_STATES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"finality_state must be one of {DISPUTE_EXTERNAL_PROCEEDING_FINALITY_STATES}")

    transition_external_proceeding(proceeding, "DECIDED")
    proceeding.decision_date = decision_date
    proceeding.finality_state = finality_state
    proceeding.outcome_evidence_id = outcome_evidence_id
    proceeding.decided_by_admin_id = admin.id

    for claim_id in claim_ids_for_proceeding(db, proceeding):
        claim = db.get(DisputeResolutionClaim, claim_id)
        if not claim:
            continue
        transition_claim(claim, outcome, note="external proceeding decision recorded")
        claim.outcome = outcome
        claim.reason_code = reason_code
        claim.decided_at = datetime.now(timezone.utc)
        claim.decided_by_admin_id = admin.id
        record_dispute_decision(
            db, claim, outcome=outcome, basis="EXTERNAL_PROCEEDING_DECIDED", authority="external_proceeding",
            decided_by_admin_id=admin.id, reason_code=reason_code, external_proceeding_id=proceeding.id,
        )

    _sync_case_status_after_claim_change(proceeding.case)

    db.commit()
    db.refresh(proceeding)
    return proceeding
