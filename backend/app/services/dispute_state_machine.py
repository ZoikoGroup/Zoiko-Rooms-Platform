"""ZR-ENG-CLR-010 Section 22: independent state machines for
DisputeResolutionCase and DisputeResolutionClaim (AC-23 -- "Case status,
claim status, financial hold... are separate objects"). Same shape as
app/services/booking_change_state_machine.py: one status tuple, one
transition-graph dict and a mutator that raises 409 on an illegal jump
rather than letting a caller write any string into `.status`.

LEGAL_REVIEW_REQUIRED is a case status this MVP adds beyond Section 22's
literal list (same "add the state this build actually produces, document
why" discipline booking_change_state_machine.py already used for
CONFLICT/FAILED) -- it's the case-level home for AC-42's fail-closed
resolver outcome, not a state the doc names directly."""

from __future__ import annotations

from fastapi import HTTPException, status

DISPUTE_CASE_STATUSES = (
    "SUBMITTED", "TRIAGED", "LEGAL_REVIEW_REQUIRED", "IN_PROGRESS",
    "PARTIALLY_RESOLVED", "RESOLVED", "CLOSED", "ON_HOLD", "EXTERNAL_PENDING", "REOPENED",
)

_CASE_TRANSITIONS: dict[str, frozenset[str]] = {
    "SUBMITTED": frozenset({"TRIAGED", "LEGAL_REVIEW_REQUIRED", "ON_HOLD"}),
    "TRIAGED": frozenset({"IN_PROGRESS", "ON_HOLD", "EXTERNAL_PENDING", "LEGAL_REVIEW_REQUIRED"}),
    "LEGAL_REVIEW_REQUIRED": frozenset({"TRIAGED", "IN_PROGRESS", "ON_HOLD"}),
    # CLOSED is reachable directly from IN_PROGRESS/PARTIALLY_RESOLVED/
    # ON_HOLD/EXTERNAL_PENDING only for QA-Q45's force-close-while-
    # externally-pending path (crud/disputes.py:close_case's own
    # force_close_reason precondition gates when that edge is actually
    # taken -- this graph only says the jump is structurally legal).
    "IN_PROGRESS": frozenset({"PARTIALLY_RESOLVED", "RESOLVED", "ON_HOLD", "EXTERNAL_PENDING", "CLOSED"}),
    "PARTIALLY_RESOLVED": frozenset({"IN_PROGRESS", "RESOLVED", "ON_HOLD", "EXTERNAL_PENDING", "CLOSED"}),
    "RESOLVED": frozenset({"CLOSED", "REOPENED"}),
    "CLOSED": frozenset({"REOPENED"}),  # AC-31: a closed case can be reopened without deleting its closure event.
    "ON_HOLD": frozenset({"TRIAGED", "IN_PROGRESS", "PARTIALLY_RESOLVED", "CLOSED"}),
    "EXTERNAL_PENDING": frozenset({"IN_PROGRESS", "PARTIALLY_RESOLVED", "RESOLVED", "CLOSED"}),
    "REOPENED": frozenset({"IN_PROGRESS"}),
}

DISPUTE_CLAIM_STATUSES = (
    "OPEN", "RESPONSE_DUE", "EVIDENCE", "NEGOTIATION", "INTERNAL_REVIEW",
    "EXTERNAL_REFERRAL", "UPHELD", "PARTLY_UPHELD", "NOT_UPHELD", "SETTLED", "WITHDRAWN",
)

# "Terminal" here means "counts as a resolved outcome for case-aggregation
# purposes" (crud/disputes.py's _sync_case_status_after_claim_change and
# close_case) -- it does NOT mean the state machine has no legal exit from
# it. Section 26/AC-31/AC-34 require exactly the opposite: a decided claim
# must be reopenable. See the reverse edges below.
CLAIM_TERMINAL_STATUSES = frozenset({"UPHELD", "PARTLY_UPHELD", "NOT_UPHELD", "SETTLED", "WITHDRAWN"})

_CLAIM_TRANSITIONS: dict[str, frozenset[str]] = {
    "OPEN": frozenset({"RESPONSE_DUE", "EVIDENCE", "NEGOTIATION", "INTERNAL_REVIEW", "EXTERNAL_REFERRAL", "WITHDRAWN"}),
    "RESPONSE_DUE": frozenset({"EVIDENCE", "NEGOTIATION", "INTERNAL_REVIEW", "EXTERNAL_REFERRAL", "WITHDRAWN"}),
    "EVIDENCE": frozenset({"NEGOTIATION", "INTERNAL_REVIEW", "EXTERNAL_REFERRAL", "WITHDRAWN"}),
    "NEGOTIATION": frozenset({"SETTLED", "INTERNAL_REVIEW", "EXTERNAL_REFERRAL", "WITHDRAWN"}),
    "INTERNAL_REVIEW": frozenset({"UPHELD", "PARTLY_UPHELD", "NOT_UPHELD", "EXTERNAL_REFERRAL", "WITHDRAWN"}),
    "EXTERNAL_REFERRAL": frozenset({"UPHELD", "PARTLY_UPHELD", "NOT_UPHELD", "SETTLED", "WITHDRAWN"}),
    # Section 26 reopen grounds ("material new evidence, processing error,
    # external decision, fraud finding...") reach back into EVIDENCE via
    # crud/disputes.py:reopen_case (admin, broad authority, any decided
    # claim including a voluntary settlement). AC-34's narrower party-
    # initiated review request (crud/disputes.py:request_internal_review)
    # only ever targets INTERNAL_REVIEW, and only for a Zoiko-decided (A0)
    # outcome -- never SETTLED/WITHDRAWN, which were voluntary/mutual, not
    # a Zoiko determination a party can ask Zoiko to reconsider.
    "UPHELD": frozenset({"EVIDENCE", "INTERNAL_REVIEW"}),
    "PARTLY_UPHELD": frozenset({"EVIDENCE", "INTERNAL_REVIEW"}),
    "NOT_UPHELD": frozenset({"EVIDENCE", "INTERNAL_REVIEW"}),
    "SETTLED": frozenset({"EVIDENCE"}),
    "WITHDRAWN": frozenset({"EVIDENCE"}),
}


EXTERNAL_PROCEEDING_STATUSES = ("FILED", "ACCEPTED", "PENDING", "DECIDED", "WITHDRAWN", "DISMISSED")

EXTERNAL_PROCEEDING_TERMINAL_STATUSES = frozenset({"DECIDED", "WITHDRAWN", "DISMISSED"})

_PROCEEDING_TRANSITIONS: dict[str, frozenset[str]] = {
    # FILED/ACCEPTED can each jump straight to DECIDED -- an admin recording
    # a decision as soon as it's known shouldn't be forced to first
    # backfill every intermediate tracking step.
    "FILED": frozenset({"ACCEPTED", "PENDING", "DECIDED", "DISMISSED", "WITHDRAWN"}),
    "ACCEPTED": frozenset({"PENDING", "DECIDED", "DISMISSED", "WITHDRAWN"}),
    "PENDING": frozenset({"DECIDED", "DISMISSED", "WITHDRAWN"}),
    "DECIDED": frozenset(),
    "WITHDRAWN": frozenset(),
    "DISMISSED": frozenset(),
}


DISPUTE_SETTLEMENT_STATUSES = ("SENT", "COUNTERED", "ACCEPTED", "REJECTED", "EXPIRED", "EFFECTIVE", "VOID")

_SETTLEMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    "SENT": frozenset({"ACCEPTED", "REJECTED", "COUNTERED", "EXPIRED", "VOID"}),
    "ACCEPTED": frozenset({"EFFECTIVE"}),
    "COUNTERED": frozenset(),
    "REJECTED": frozenset(),
    "EXPIRED": frozenset(),
    "EFFECTIVE": frozenset(),
    "VOID": frozenset(),
}


def transition_settlement(settlement, to_status: str, *, note: str | None = None) -> None:
    allowed = _SETTLEMENT_TRANSITIONS.get(settlement.status, frozenset())
    if to_status not in allowed:
        detail = f"Cannot move settlement #{settlement.id} from {settlement.status} to {to_status}"
        if note:
            detail += f" ({note})"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    settlement.status = to_status


# Section 22 Evidence Item state machine, adapted (see
# models/dispute_evidence.py's own comment on why this is RECEIVED ->
# VERIFIED/UNVERIFIED -> ARCHIVED rather than a literal port of the doc's
# five-state chain -- DISCLOSED/RESTRICTED already duplicate
# disclosure_class, a separate, already-built field).
_EVIDENCE_VERIFICATION_TRANSITIONS: dict[str, frozenset[str]] = {
    "RECEIVED": frozenset({"VERIFIED", "UNVERIFIED", "ARCHIVED"}),
    "VERIFIED": frozenset({"UNVERIFIED", "ARCHIVED"}),
    "UNVERIFIED": frozenset({"VERIFIED", "ARCHIVED"}),
    "ARCHIVED": frozenset(),
}


def transition_evidence_verification(evidence, to_status: str, *, note: str | None = None) -> None:
    allowed = _EVIDENCE_VERIFICATION_TRANSITIONS.get(evidence.verification_status, frozenset())
    if to_status not in allowed:
        detail = f"Cannot move evidence #{evidence.id} verification status from {evidence.verification_status} to {to_status}"
        if note:
            detail += f" ({note})"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    evidence.verification_status = to_status


def allowed_case_transitions(from_status: str) -> frozenset[str]:
    """Exposed so crud/disputes.py's derived-status sync (a real claim
    decision changed the picture, not a user-directed jump) can check
    legality before calling transition_case, instead of calling it
    speculatively and catching the 409."""
    return _CASE_TRANSITIONS.get(from_status, frozenset())


def allowed_claim_transitions(from_status: str) -> frozenset[str]:
    return _CLAIM_TRANSITIONS.get(from_status, frozenset())


def transition_case(case, to_status: str, *, note: str | None = None) -> None:
    allowed = _CASE_TRANSITIONS.get(case.status, frozenset())
    if to_status not in allowed:
        detail = f"Cannot move dispute case #{case.id} from {case.status} to {to_status}"
        if note:
            detail += f" ({note})"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    case.status = to_status


def transition_claim(claim, to_status: str, *, note: str | None = None) -> None:
    allowed = _CLAIM_TRANSITIONS.get(claim.status, frozenset())
    if to_status not in allowed:
        detail = f"Cannot move dispute claim #{claim.id} from {claim.status} to {to_status}"
        if note:
            detail += f" ({note})"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    claim.status = to_status


def transition_external_proceeding(proceeding, to_status: str, *, note: str | None = None) -> None:
    allowed = _PROCEEDING_TRANSITIONS.get(proceeding.status, frozenset())
    if to_status not in allowed:
        detail = f"Cannot move external proceeding #{proceeding.id} from {proceeding.status} to {to_status}"
        if note:
            detail += f" ({note})"
        raise HTTPException(status.HTTP_409_CONFLICT, detail)
    proceeding.status = to_status
