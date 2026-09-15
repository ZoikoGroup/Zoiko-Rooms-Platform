"""ZR-ENG-CLR-010 Section 21 ("Export: Generate indexed evidence bundle
with chronology, original filenames, hashes, source metadata...")/Section
28 ("Legal/regulatory exports must be reproducible from canonical records
without relying on a staff member's narrative summary").

This is pure aggregation over facts that already exist on real, timestamped
columns across Phases 1-5 (DisputeResolutionCase/Claim/Hold,
DisputeEvidenceItem, DisputeExternalProceeding, DisputeSettlement) -- no
new tables, no AI, no narrative generation. Section 21's "clearly separated
Zoiko-generated summaries" rule is about an AI-authored chronology; this
one is a deterministic derivation straight from canonical timestamps, so
there is nothing to mislabel as human- or AI-authored -- DisputeCaseExportRead's
own `note` field says this plainly rather than silently assuming the rule
doesn't apply.

Disclosure stays exactly what Phase 2 already enforces: `evidence_index`
reuses crud/dispute_evidence.py:list_evidence_for_case's own
viewer_is_admin gate, so a party's export can never contain an
INTERNAL_ONLY item -- this module does not re-implement that check.
QA-Q40: the same call now also takes `viewer_admin` so an admin export
excludes PRIVILEGED_RESTRICTED items unless that admin is actually
specialized into TRUST_AND_SAFETY/LEGAL_COMPLIANCE (or super_admin) --
see list_evidence_for_case's own `_admin_may_see_privileged` gate.
Financial-hold chronology entries get the same
split: an admin sees `authority_basis` and who created/released the hold;
a party only ever sees amount/currency (Section 11's "Money status" is
explicitly party-visible, but the internal legal basis and actor identity
are not)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.crud import dispute_evidence as evidence_crud
from app.crud import dispute_external_proceeding as proceeding_crud
from app.crud import dispute_settlement as settlement_crud
from app.models.admin_user import AdminUser
from app.models.dispute import DisputeResolutionCase
from app.schemas.dispute_case_export import DisputeCaseExportRead, DisputeChronologyEvent
from app.schemas.disputes import DisputeCaseRead
from app.schemas.dispute_evidence import DisputeEvidenceRead

_EXPORT_NOTE = (
    "This export is a deterministic derivation from canonical dispute records "
    "(case, claim, evidence, settlement and external-proceeding timestamps). "
    "It is not an AI-generated or staff-authored narrative summary."
)


def _evidence_to_read(db: Session, evidence) -> DisputeEvidenceRead:
    data = DisputeEvidenceRead.model_validate(evidence)
    return data.model_copy(update={"claim_ids": evidence_crud.claim_ids_for_evidence(db, evidence)})


def _chronology_events(
    db: Session, case: DisputeResolutionCase, *, viewer_is_admin: bool, viewer_admin: AdminUser | None = None,
) -> list[DisputeChronologyEvent]:
    events: list[DisputeChronologyEvent] = []

    def add(timestamp: datetime | None, event_type: str, summary: str) -> None:
        if timestamp is not None:
            events.append(DisputeChronologyEvent(timestamp=timestamp, event_type=event_type, summary=summary))

    add(case.opened_at, "case.opened", f"Case #{case.id} opened ({case.primary_claim_family}, {case.severity}).")
    add(case.closed_at, "case.closed", f"Case #{case.id} closed.")
    add(case.reopened_at, "case.reopened", f"Case #{case.id} reopened ({case.reopen_grounds}).")

    for claim in case.claims:
        add(claim.created_at, "claim.added", f"Claim #{claim.id} ({claim.claim_family}/{claim.claim_code}) added by {claim.claimant_role.lower()}.")
        # Section 19/22/26: every decision this claim has ever had, not just
        # the current one -- a reopen + re-decision overwrites claim.outcome/
        # decided_at in place (models/dispute_decision.py's own docstring),
        # so the append-only DisputeDecision history is this export's only
        # source for a claim decided more than once.
        for decision in claim.decisions:
            add(decision.decided_at, "claim.decided", f"Claim #{claim.id} decided: {decision.outcome} ({decision.decision_basis}).")
        for hold in claim.holds:
            if viewer_is_admin:
                add(hold.created_at, "financial_hold.opened", f"Financial hold #{hold.id} opened on claim #{claim.id} for {hold.amount} {hold.currency} ({hold.authority_basis}).")
                if hold.released_at is not None:
                    add(hold.released_at, "financial_hold.released", f"Financial hold #{hold.id} released ({hold.release_reason}).")
            else:
                add(hold.created_at, "financial_hold.opened", f"A financial hold of {hold.amount} {hold.currency} was placed on claim #{claim.id}.")
                if hold.released_at is not None:
                    add(hold.released_at, "financial_hold.released", f"The financial hold on claim #{claim.id} was released.")

    for evidence in evidence_crud.list_evidence_for_case(db, case, viewer_is_admin=viewer_is_admin, viewer_admin=viewer_admin):
        add(evidence.created_at, "evidence.uploaded", f"Evidence #{evidence.id} ({evidence.provenance}) added: {evidence.original_filename or evidence.note_text[:60]}.")

    for settlement in settlement_crud.list_settlements_for_case(db, case):
        add(settlement.offered_at, "settlement.proposed", f"Settlement #{settlement.id} proposed by {settlement.proposed_by_role.lower()}.")
        if settlement.responded_at is not None:
            add(settlement.responded_at, "settlement.responded", f"Settlement #{settlement.id} response recorded: {settlement.status}.")
        if settlement.effective_at is not None:
            add(settlement.effective_at, "settlement.effective", f"Settlement #{settlement.id} became effective.")

    for proceeding in proceeding_crud.list_proceedings_for_case(db, case):
        add(proceeding.created_at, "external_proceeding.filed", f"External proceeding #{proceeding.id} filed with {proceeding.authority_type}.")
        if proceeding.decision_date is not None:
            add(
                # decision_date is a Date, not a DateTime -- combined with
                # midnight UTC so it sorts consistently with every other
                # (timezone-aware) event's timestamp.
                datetime.combine(proceeding.decision_date, datetime.min.time(), tzinfo=timezone.utc),
                "external_proceeding.decided",
                f"External proceeding #{proceeding.id} decided.",
            )

    events.sort(key=lambda event: event.timestamp)
    return events


def build_case_export(
    db: Session, case: DisputeResolutionCase, *, viewer_is_admin: bool, viewer_admin: AdminUser | None = None,
) -> DisputeCaseExportRead:
    return DisputeCaseExportRead(
        case=DisputeCaseRead.model_validate(case),
        evidence_index=[
            _evidence_to_read(db, e)
            for e in evidence_crud.list_evidence_for_case(db, case, viewer_is_admin=viewer_is_admin, viewer_admin=viewer_admin)
        ],
        chronology=_chronology_events(db, case, viewer_is_admin=viewer_is_admin, viewer_admin=viewer_admin),
        generated_at=datetime.now(timezone.utc),
        note=_EXPORT_NOTE,
    )
