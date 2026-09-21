import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import settings
from app.crud import dispute_deadline as deadline_crud
from app.crud import habitability_incident as habitability_crud
from app.crud import notification as notif_crud
from app.crud.market_policy import jurisdiction_code_for_occupancy, resolve_market_policy
from app.models.admin_user import AdminUser
from app.models.booking_change_request import BookingChangeRequest
from app.models.leasing import Agreement
from app.models.occupancy_activation import OccupancyHandoverEvent
from app.models.refund_entitlement import RefundEntitlement
from app.models.sublet_request import SubletRequest
from app.models.termination_case import TerminationCase
from app.models.dispute import (
    DISPUTE_CASE_REOPEN_GROUNDS,
    DISPUTE_CASE_TEAMS,
    DISPUTE_CLAIM_FAMILIES,
    DisputeResolutionCase,
    DisputeResolutionClaim,
    DisputeResolutionHold,
)
from app.models.dispute_deadline import DisputeDeadline
from app.models.dispute_decision import DISPUTE_INTERNAL_DECISION_REASON_CATEGORIES, DisputeDecision
from app.models.dispute_evidence import DisputeEvidenceClaimLink, DisputeEvidenceItem
from app.models.dispute_external_proceeding import DisputeExternalProceeding, DisputeExternalProceedingClaimLink
from app.models.dispute_party import DisputeParty
from app.models.finance import Obligation
from app.models.guest import Guest
from app.models.membership import Membership
from app.models.occupancy import Occupancy
from app.models.property import Property
from app.schemas.disputes import (
    DisputeCaseCreate,
    DisputeClaimCreate,
    DisputeClaimDecide,
    DisputeFinancialHoldCreate,
    DisputeFinancialHoldRelease,
)
from app.services.booking_change_state_machine import TERMINAL_STATUSES as BCR_TERMINAL_STATUSES
from app.services.dispute_forum_resolver import compute_severity, resolve_claim_authority
from app.services.dispute_state_machine import (
    CLAIM_TERMINAL_STATUSES,
    EXTERNAL_PROCEEDING_TERMINAL_STATUSES,
    allowed_case_transitions,
    transition_case,
    transition_claim,
)


def _property_id_for_occupancy(occupancy: Occupancy) -> int | None:
    if occupancy.listing and occupancy.listing.room and occupancy.listing.room.property:
        return occupancy.listing.room.property.id
    return None


def _resolve_market_policy_for_occupancy(db: Session, occupancy: Occupancy):
    """ZR-ENG-CLR-010 Section 7: jurisdiction-aware forum resolution.
    Unlike deposit/sublet code (which correctly fails closed via
    resolve_market_policy's own 409 when no pack is configured), opening a
    dispute must stay possible even where market-pack configuration is
    incomplete -- this catches that 409 and returns None instead, letting
    dispute_forum_resolver.py fall back to its own static defaults rather
    than blocking case/claim intake outright."""
    try:
        jurisdiction_code = jurisdiction_code_for_occupancy(occupancy)
        return resolve_market_policy(db, jurisdiction_code)
    except HTTPException:
        return None


def _sync_case_status_after_claim_change(case: DisputeResolutionCase) -> None:
    """A claim decision/addition changes the derived case status -- this is
    not a user-directed transition request, so it takes whatever legal path
    the state machine allows to get there (through IN_PROGRESS first, if
    needed) rather than raising or forcing an illegal jump."""
    claims = case.claims
    if not claims:
        return
    all_terminal = all(c.status in CLAIM_TERMINAL_STATUSES for c in claims)
    any_terminal = any(c.status in CLAIM_TERMINAL_STATUSES for c in claims)
    target = "RESOLVED" if all_terminal else ("PARTIALLY_RESOLVED" if any_terminal else "IN_PROGRESS")
    if case.status == target:
        return
    if target not in allowed_case_transitions(case.status) and "IN_PROGRESS" in allowed_case_transitions(case.status):
        transition_case(case, "IN_PROGRESS")
    if target in allowed_case_transitions(case.status):
        transition_case(case, target)


def _notify_case_opened(db: Session, case: DisputeResolutionCase) -> None:
    title = "A dispute has been opened"
    message = f"A {case.primary_claim_family.lower().replace('_', ' ')} dispute has been opened and is under review."
    if case.opened_by_guest_id and case.property_id:
        prop = db.get(Property, case.property_id)
        if prop:
            notif_crud.notify_user_by_party(
                db, prop.owner_party_id, title=title, message=message, notification_type="dispute_case.opened",
            )
    if case.opened_by_party_id and case.occupancy_id:
        occ = db.get(Occupancy, case.occupancy_id)
        if occ and occ.guest:
            notif_crud.notify_user_by_guest(
                db, occ.guest, title=title, message=message, notification_type="dispute_case.opened_for_renter",
            )
    notif_crud.notify_all_super_admins(
        db,
        title="New dispute case opened",
        message=f"A {case.severity} {case.primary_claim_family.lower()} dispute (#{case.id}) requires review.",
        notification_type="dispute_case.opened_admin",
        related_entity_type="dispute_resolution_case", related_entity_id=str(case.id),
    )


def _notify_case_participants(db: Session, case: DisputeResolutionCase, *, title: str, message: str, notification_type: str) -> None:
    """Section 10 gap: the same 'notify whichever side is the party, not
    the opener' shape as _notify_case_opened above, reused for every other
    silent event this system previously never told anyone about (claim
    decisions, case close/reopen, settlement accept/reject)."""
    if case.property_id:
        prop = db.get(Property, case.property_id)
        if prop:
            notif_crud.notify_user_by_party(db, prop.owner_party_id, title=title, message=message, notification_type=notification_type)
    if case.occupancy_id:
        occ = db.get(Occupancy, case.occupancy_id)
        if occ and occ.guest:
            notif_crud.notify_user_by_guest(db, occ.guest, title=title, message=message, notification_type=notification_type)


def _build_platform_snapshot(db: Session, occupancy: Occupancy) -> dict:
    """ZR-ENG-CLR-010 Section 21 'Platform snapshots'/Section 2 Governing
    Doctrine: freeze the occupancy/agreement/deposit facts as they stood at
    case-open time, so a later agreement amendment or deposit release can
    never quietly rewrite what the dispute was actually opened against."""
    snapshot: dict = {
        "occupancy_id": occupancy.id,
        "occupancy_status": occupancy.status,
        "listing_id": occupancy.listing_id,
        "room_id": occupancy.room_id,
        "move_in_date": occupancy.move_in_date,
        "move_out_date": occupancy.move_out_date,
    }
    agreement = occupancy.offer.agreement if occupancy.offer else None
    if agreement is not None:
        snapshot["agreement_id"] = agreement.id
        snapshot["agreement_version"] = agreement.version
        snapshot["agreement_status"] = agreement.status

    deposit_obligation = db.scalar(
        select(Obligation).where(Obligation.occupancy_id == occupancy.id, Obligation.obligation_type == "DEPOSIT")
    )
    if deposit_obligation is not None and deposit_obligation.deposit_record is not None:
        deposit_record = deposit_obligation.deposit_record
        snapshot["deposit_record_id"] = deposit_record.id
        snapshot["deposit_status"] = deposit_record.status
        snapshot["deposit_held_amount"] = deposit_record.held_amount

    return snapshot


def _create_self_dispute_parties(
    db: Session, case: DisputeResolutionCase, *, guest: Guest | None, party_id: int | None, occupancy: Occupancy | None,
) -> None:
    """Section 23 dispute_party -- one SELF row per side, recorded at
    case-open time. authority_verified_at is set immediately: the
    ownership check earlier in open_case already verified whoever opened
    the case, and the counterparty is simply the other side of that same
    already-verified occupancy/property relationship. Only the opener's
    own row is created when there's no occupancy to derive a counterparty
    from (a pure ZOIKO_SERVICE case, same nullability case
    _property_id_for_occupancy already documents)."""
    now = datetime.now(timezone.utc)
    if guest is not None:
        db.add(DisputeParty(case_id=case.id, party_role="RENTER", guest_id=guest.id, representation_type="SELF", authority_verified_at=now))
        if occupancy is not None:
            counterparty_party_id = occupancy.listing.room.property.owner_party_id
            db.add(DisputeParty(case_id=case.id, party_role="HOST", party_id=counterparty_party_id, representation_type="SELF", authority_verified_at=now))
    else:
        db.add(DisputeParty(case_id=case.id, party_role="HOST", party_id=party_id, representation_type="SELF", authority_verified_at=now))
        if occupancy is not None:
            db.add(DisputeParty(case_id=case.id, party_role="RENTER", guest_id=occupancy.guest_id, representation_type="SELF", authority_verified_at=now))


def _create_platform_snapshot_evidence(
    db: Session, case: DisputeResolutionCase, claim: DisputeResolutionClaim, occupancy: Occupancy,
) -> None:
    snapshot = _build_platform_snapshot(db, occupancy)
    # No uploaded_by_* set -- this is a legitimate system-generated record
    # (provenance=SYSTEM_RECORD), not a human upload, so
    # crud/dispute_evidence.py:upload_evidence's "exactly one uploader"
    # validation (scoped to human uploads) intentionally isn't used here.
    evidence = DisputeEvidenceItem(
        case_id=case.id,
        provenance="SYSTEM_RECORD",
        note_text=json.dumps(snapshot, sort_keys=True, default=str),
        disclosure_class="PARTY_VISIBLE",
    )
    db.add(evidence)
    db.flush()
    db.add(DisputeEvidenceClaimLink(evidence_id=evidence.id, claim_id=claim.id))


def _create_party_response_deadline(db: Session, case: DisputeResolutionCase, claim: DisputeResolutionClaim, market_policy_pack) -> None:
    """AC-29: "Deadlines are computed from the relevant forum pack, with
    reminders and extension audit." One PARTY_RESPONSE deadline per claim,
    computed from the resolved market pack's dispute_response_window_days
    when one is resolvable, else _DEFAULT_PARTY_RESPONSE_WINDOW_DAYS
    (Section 26's own "recommended commercial default 5 business days"
    text). Same SYSTEM_DEFAULT/reminder-computed shape as decide_claim's
    own INTERNAL_REVIEW deadline."""
    window_days = market_policy_pack.dispute_response_window_days if market_policy_pack is not None else _DEFAULT_PARTY_RESPONSE_WINDOW_DAYS
    created_at = claim.created_at
    due_at = created_at + timedelta(days=window_days)
    db.add(DisputeDeadline(
        case_id=case.id, claim_id=claim.id, deadline_type="PARTY_RESPONSE",
        due_at=due_at, status="PENDING", source="SYSTEM_DEFAULT",
        created_at=created_at, reminder_at=deadline_crud.compute_reminder_at(created_at, due_at),
    ))


def _default_assigned_team(resolution, severity: str) -> str:
    """Section 12/19/23 auto-assignment -- deterministic from facts the
    forum resolver/severity triage already computed, no new judgment call.
    Safety takes priority over legal-review routing (a safety-flagged
    claim that also happens to be unresolved-forum still goes to Trust &
    Safety, never Legal)."""
    if resolution.authority_class == "A6" or severity == "SEV-0":
        return "TRUST_AND_SAFETY"
    if resolution.confidence == "LEGAL_REVIEW_REQUIRED":
        return "LEGAL_COMPLIANCE"
    return "DISPUTE_OPERATIONS"


# AC-38: the claim codes Section 5's own taxonomy lists under Sublet/
# occupancy ("ACCESS, LOCKOUT, HOLDOVER") that are about possession/access,
# not sublet consent -- these prefer linking to the occupancy's handover
# evidence over a SubletRequest, when one exists.
_OCCUPANCY_ACCESS_CLAIM_CODES = ("LOCKOUT", "ILLEGAL_LOCKOUT", "ACCESS", "HOLDOVER")


def _build_source_record_snapshot(db: Session, source_record_type: str, record) -> dict:
    """AC-35/37/38: the specific fields each AC names, frozen at link time
    -- see DisputeResolutionClaim.source_record_snapshot's own docstring
    for why this is a snapshot, not a live join."""
    if source_record_type == "HABITABILITY_INCIDENT":
        return {"severity": record.severity, "status": record.status, "description": record.description[:500]}
    if source_record_type == "BOOKING_CHANGE_REQUEST":
        return {
            "change_type": record.change_type,
            "status": record.status,
            "proposal_hash": record.proposal_hash,
            "proposed_start_date": record.proposed_start_date.isoformat() if record.proposed_start_date else None,
            "proposed_end_date": record.proposed_end_date.isoformat() if record.proposed_end_date else None,
        }
    if source_record_type == "SUBLET_REQUEST":
        return {
            "arrangement_type": record.arrangement_type,
            "authority_evidence_ref": record.authority_evidence_ref,
            "status": record.status,
        }
    if source_record_type == "OCCUPANCY_HANDOVER_EVENT":
        return {
            "event_type": record.event_type,
            "actor_kind": record.actor_kind,
            "evidence_ref": record.evidence_ref,
            "created_at": record.created_at.isoformat(),
        }
    if source_record_type == "TERMINATION_CASE":
        latest_entitlement = db.scalar(
            select(RefundEntitlement)
            .where(RefundEntitlement.termination_case_id == record.id)
            .order_by(RefundEntitlement.version.desc())
        )
        return {
            "cause_code": record.cause_code,
            "status": record.status,
            "effective_termination_date": record.effective_termination_date.isoformat() if record.effective_termination_date else None,
            # AC-36 "no-double-recovery": the fact this claim's decide_claim
            # guard actually reads -- see _assert_no_termination_double_recovery.
            "refund_entitlement_status": latest_entitlement.status if latest_entitlement else None,
        }
    return {}


def _resolve_source_record(
    db: Session, occupancy: Occupancy | None, claim_family: str, claim_code: str = "",
) -> tuple[str | None, str | None, dict]:
    """AC-35/36/37/38: auto-links a new claim to the most relevant already-
    existing record for its occupancy -- a still-open/pending one preferred
    over a decided/terminal one when several exist, else the most recent --
    and freezes the specific facts each AC names into source_record_snapshot.
    Server-resolved rather than client-supplied: the client would otherwise
    need to already know which of several heterogeneous tables/ids to name,
    and correctness would depend on trusting a value it sent us (same
    "resolve facts server-side" discipline as
    _resolve_market_policy_for_occupancy/_property_id_for_occupancy above)."""
    if occupancy is None:
        return None, None, {}

    if claim_family == "PROPERTY_CONDITION":
        incidents = habitability_crud.list_habitability_incidents_for_occupancy(db, occupancy)
        if not incidents:
            return None, None, {}
        chosen = next((i for i in incidents if i.status == "OPEN"), incidents[0])
        return "HABITABILITY_INCIDENT", str(chosen.id), _build_source_record_snapshot(db, "HABITABILITY_INCIDENT", chosen)

    if claim_family == "BOOKING_AGREEMENT" and claim_code in ("CANCELLATION", "EARLY_TERMINATION"):
        termination_case = db.scalar(
            select(TerminationCase)
            .where(TerminationCase.occupancy_id == occupancy.id)
            .order_by(TerminationCase.id.desc())
        )
        if termination_case is None:
            return None, None, {}
        return "TERMINATION_CASE", str(termination_case.id), _build_source_record_snapshot(db, "TERMINATION_CASE", termination_case)

    if claim_family == "BOOKING_AGREEMENT":
        agreement = db.scalar(select(Agreement).where(Agreement.offer_id == occupancy.offer_id))
        if agreement is None:
            return None, None, {}
        requests = list(
            db.scalars(
                select(BookingChangeRequest)
                .where(BookingChangeRequest.agreement_id == agreement.id)
                .order_by(BookingChangeRequest.created_at.desc())
            )
        )
        if not requests:
            return None, None, {}
        chosen = next((r for r in requests if r.status not in BCR_TERMINAL_STATUSES), requests[0])
        return "BOOKING_CHANGE_REQUEST", str(chosen.id), _build_source_record_snapshot(db, "BOOKING_CHANGE_REQUEST", chosen)

    if claim_family == "SUBLET_OCCUPANCY":
        if claim_code in _OCCUPANCY_ACCESS_CLAIM_CODES:
            handover_events = list(
                db.scalars(
                    select(OccupancyHandoverEvent)
                    .where(OccupancyHandoverEvent.occupancy_id == occupancy.id)
                    .order_by(OccupancyHandoverEvent.created_at.desc())
                )
            )
            if handover_events:
                chosen = handover_events[0]
                return (
                    "OCCUPANCY_HANDOVER_EVENT", str(chosen.id),
                    _build_source_record_snapshot(db, "OCCUPANCY_HANDOVER_EVENT", chosen),
                )
            # No handover evidence on file -- fall through to a SubletRequest
            # if one exists; otherwise both fields stay null (AC-42's own
            # discipline: no evidence to point at is not invented).

        requests = list(
            db.scalars(
                select(SubletRequest)
                .where(SubletRequest.current_occupancy_id == occupancy.id)
                .order_by(SubletRequest.created_at.desc())
            )
        )
        if not requests:
            return None, None, {}
        chosen = next((r for r in requests if r.status in ("pending_verification", "pending_admin_review")), requests[0])
        return "SUBLET_REQUEST", str(chosen.id), _build_source_record_snapshot(db, "SUBLET_REQUEST", chosen)

    return None, None, {}


def open_case(
    db: Session, data: DisputeCaseCreate, *, guest: Guest | None = None, party_id: int | None = None,
) -> DisputeResolutionCase:
    if (guest is None) == (party_id is None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A case must be opened by exactly one of a renter or a host")
    claimant_role = "RENTER" if guest is not None else "HOST"

    occupancy: Occupancy | None = None
    property_id: int | None = None
    if data.occupancy_id is not None:
        occupancy = db.get(Occupancy, data.occupancy_id)
        if not occupancy:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
        property_id = _property_id_for_occupancy(occupancy)
        if guest is not None and occupancy.guest_id != guest.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only open a dispute on your own occupancy")
        if party_id is not None and (property_id is None or occupancy.listing.room.property.owner_party_id != party_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only open a dispute on your own property's occupancy")

    if data.claim.claim_family not in DISPUTE_CLAIM_FAMILIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown claim family '{data.claim.claim_family}'")

    market_policy_pack = _resolve_market_policy_for_occupancy(db, occupancy) if occupancy is not None else None
    resolution = resolve_claim_authority(
        data.claim.claim_family, data.claim.claim_code, safety_flag=data.claim.safety_flag, market_policy_pack=market_policy_pack,
    )
    severity = compute_severity(data.claim.claim_family, data.claim.claim_code, safety_flag=data.claim.safety_flag)

    case = DisputeResolutionCase(
        occupancy_id=occupancy.id if occupancy else None,
        property_id=property_id,
        opened_by_guest_id=guest.id if guest is not None else None,
        opened_by_party_id=party_id,
        severity=severity,
        status="SUBMITTED",
        primary_claim_family=data.claim.claim_family,
        # Section 4/6: A0/A1 stay Zoiko-internal/bilateral; anything else
        # (including an unresolved None) is not a clean internal matter.
        external_dependency_flag=resolution.authority_class not in ("A0", "A1"),
        assigned_team=_default_assigned_team(resolution, severity),
    )
    db.add(case)
    db.flush()

    source_record_type, source_record_id, source_record_snapshot = _resolve_source_record(
        db, occupancy, data.claim.claim_family, data.claim.claim_code,
    )
    claim = DisputeResolutionClaim(
        case_id=case.id,
        claim_code=data.claim.claim_code,
        claim_family=data.claim.claim_family,
        claimant_role=claimant_role,
        amount=data.claim.amount,
        currency=data.claim.currency,
        requested_remedy=data.claim.requested_remedy,
        authority_class=resolution.authority_class,
        resolver_confidence=resolution.confidence,
        resolver_notes=resolution.notes,
        policy_pack_id=resolution.policy_pack_id,
        policy_pack_version=resolution.policy_pack_version,
        source_record_type=source_record_type,
        source_record_id=source_record_id,
        source_record_snapshot=source_record_snapshot,
        status="OPEN",
    )
    db.add(claim)
    db.flush()

    _create_self_dispute_parties(db, case, guest=guest, party_id=party_id, occupancy=occupancy)

    if occupancy is not None:
        _create_platform_snapshot_evidence(db, case, claim, occupancy)

    _create_party_response_deadline(db, case, claim, market_policy_pack)

    transition_case(case, "LEGAL_REVIEW_REQUIRED" if resolution.confidence == "LEGAL_REVIEW_REQUIRED" else "TRIAGED")

    _notify_case_opened(db, case)

    db.commit()
    db.refresh(case)
    return case


def add_claim(db: Session, case: DisputeResolutionCase, data: DisputeClaimCreate, *, claimant_role: str) -> DisputeResolutionClaim:
    if case.status == "CLOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot add a claim to a closed dispute case")
    if data.claim_family not in DISPUTE_CLAIM_FAMILIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown claim family '{data.claim_family}'")

    market_policy_pack = _resolve_market_policy_for_occupancy(db, case.occupancy) if case.occupancy is not None else None
    resolution = resolve_claim_authority(
        data.claim_family, data.claim_code, safety_flag=data.safety_flag, market_policy_pack=market_policy_pack,
    )

    source_record_type, source_record_id, source_record_snapshot = _resolve_source_record(
        db, case.occupancy, data.claim_family, data.claim_code,
    )
    claim = DisputeResolutionClaim(
        case_id=case.id,
        claim_code=data.claim_code,
        claim_family=data.claim_family,
        claimant_role=claimant_role,
        amount=data.amount,
        currency=data.currency,
        requested_remedy=data.requested_remedy,
        authority_class=resolution.authority_class,
        resolver_confidence=resolution.confidence,
        resolver_notes=resolution.notes,
        policy_pack_id=resolution.policy_pack_id,
        policy_pack_version=resolution.policy_pack_version,
        source_record_type=source_record_type,
        source_record_id=source_record_id,
        source_record_snapshot=source_record_snapshot,
        status="OPEN",
    )
    db.add(claim)
    db.flush()

    _create_party_response_deadline(db, case, claim, market_policy_pack)

    if resolution.authority_class not in ("A0", "A1"):
        case.external_dependency_flag = True

    if resolution.confidence == "LEGAL_REVIEW_REQUIRED" and "LEGAL_REVIEW_REQUIRED" in allowed_case_transitions(case.status):
        transition_case(case, "LEGAL_REVIEW_REQUIRED")
    else:
        _sync_case_status_after_claim_change(case)

    db.commit()
    db.refresh(claim)
    return claim


def compute_money_status(case: DisputeResolutionCase) -> list[dict]:
    """AC-32/Section 11 'Money status': "Clearly separate 'amount
    disputed,' 'amount temporarily held,' 'amount undisputed,' and
    '...settled.'" Grouped per currency, since a case's claims aren't
    guaranteed to share one (each DisputeResolutionClaim carries its own
    `currency`). Definitions are this system's own honest scope, not a
    full transaction-level reconciliation (this system has no record of
    the total underlying transaction/deposit/payout amount beyond what a
    claim itself names):
      - amount_disputed: claims still non-terminal (actively being disputed).
      - amount_held: currently-restrained financial holds (PROPOSED/ACTIVE/
        RELEASE_PENDING) on any of the case's claims.
      - amount_settled: claims whose outcome is SETTLED.
      - amount_undisputed: terminal, non-settled claims that were never
        actually restrained by any hold (NOT_UPHELD/WITHDRAWN/UPHELD/
        PARTLY_UPHELD with no hold ever opened against them) -- the
        claimed amount turned out not to require restraint."""
    buckets: dict[str, dict[str, float]] = {}

    def bucket(currency: str) -> dict[str, float]:
        return buckets.setdefault(
            currency, {"amount_disputed": 0.0, "amount_held": 0.0, "amount_undisputed": 0.0, "amount_settled": 0.0},
        )

    for claim in case.claims:
        if claim.amount is None:
            continue
        amount = float(claim.amount)
        b = bucket(claim.currency)
        if claim.status not in CLAIM_TERMINAL_STATUSES:
            b["amount_disputed"] += amount
        elif claim.outcome == "SETTLED":
            b["amount_settled"] += amount
        elif not claim.holds:
            b["amount_undisputed"] += amount

    for claim in case.claims:
        for hold in claim.holds:
            if hold.status in ("PROPOSED", "ACTIVE", "RELEASE_PENDING"):
                bucket(hold.currency)["amount_held"] += float(hold.amount)

    return [{"currency": currency, **values} for currency, values in buckets.items()]


def get_case_or_404(db: Session, case_id: int) -> DisputeResolutionCase:
    case = db.get(DisputeResolutionCase, case_id)
    if not case:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute case not found")
    return case


def get_claim_or_404(db: Session, claim_id: int) -> DisputeResolutionClaim:
    claim = db.get(DisputeResolutionClaim, claim_id)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute claim not found")
    return claim


def list_decisions_for_case(db: Session, case: DisputeResolutionCase) -> list[DisputeDecision]:
    return list(
        db.scalars(select(DisputeDecision).where(DisputeDecision.case_id == case.id).order_by(DisputeDecision.decided_at))
    )


def get_hold_or_404(db: Session, hold_id: int) -> DisputeResolutionHold:
    hold = db.get(DisputeResolutionHold, hold_id)
    if not hold:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute financial hold not found")
    return hold


def assert_guest_can_access_case(case: DisputeResolutionCase, guest: Guest) -> None:
    if case.opened_by_guest_id == guest.id:
        return
    if case.occupancy is not None and case.occupancy.guest_id == guest.id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this dispute case")


def assert_party_can_access_case(case: DisputeResolutionCase, party_id: int) -> None:
    if case.opened_by_party_id == party_id:
        return
    if case.property is not None and case.property.owner_party_id == party_id:
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this dispute case")


def list_cases_for_guest(db: Session, guest: Guest) -> list[DisputeResolutionCase]:
    query = (
        select(DisputeResolutionCase)
        .join(Occupancy, Occupancy.id == DisputeResolutionCase.occupancy_id, isouter=True)
        .where(or_(DisputeResolutionCase.opened_by_guest_id == guest.id, Occupancy.guest_id == guest.id))
        .order_by(DisputeResolutionCase.opened_at.desc())
    )
    return list(db.scalars(query))


def list_cases_for_party(db: Session, party_id: int) -> list[DisputeResolutionCase]:
    query = (
        select(DisputeResolutionCase)
        .join(Property, Property.id == DisputeResolutionCase.property_id, isouter=True)
        .where(or_(DisputeResolutionCase.opened_by_party_id == party_id, Property.owner_party_id == party_id))
        .order_by(DisputeResolutionCase.opened_at.desc())
    )
    return list(db.scalars(query))


def _admin_party_ids(db: Session, admin: AdminUser) -> list[int]:
    return list(
        db.scalars(select(Membership.party_id).where(Membership.admin_user_id == admin.id, Membership.status == "active"))
    )


def list_cases_for_admin(db: Session, admin: AdminUser, *, team: str | None = None) -> list[DisputeResolutionCase]:
    """super_admin sees every case; a provider-scoped admin sees only cases
    tied to a party they hold an active Membership on -- same
    Membership-based scoping assert_provider_access (crud/party.py) already
    uses, applied here to listing rather than blocking a single action.
    `team` (Section 12 'Queue / filters... assigned team') narrows either
    branch further when given."""
    if admin.role == "super_admin":
        query = select(DisputeResolutionCase).order_by(DisputeResolutionCase.opened_at.desc())
        if team is not None:
            query = query.where(DisputeResolutionCase.assigned_team == team)
        return list(db.scalars(query))

    party_ids = _admin_party_ids(db, admin)
    query = (
        select(DisputeResolutionCase)
        .join(Property, Property.id == DisputeResolutionCase.property_id, isouter=True)
        .where(or_(DisputeResolutionCase.opened_by_party_id.in_(party_ids), Property.owner_party_id.in_(party_ids)))
        .order_by(DisputeResolutionCase.opened_at.desc())
    )
    if team is not None:
        query = query.where(DisputeResolutionCase.assigned_team == team)
    return list(db.scalars(query))


def reassign_case(
    db: Session, case: DisputeResolutionCase, admin: AdminUser, *, team: str | None = None, assigned_admin_id: int | None = None,
) -> DisputeResolutionCase:
    if team is None and assigned_admin_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide a team and/or an assigned_admin_id to reassign this case")
    if team is not None and team not in DISPUTE_CASE_TEAMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown team '{team}'")
    if assigned_admin_id is not None and not db.get(AdminUser, assigned_admin_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin not found")

    if team is not None:
        case.assigned_team = team
    if assigned_admin_id is not None:
        case.assigned_admin_id = assigned_admin_id

    db.commit()
    db.refresh(case)
    return case


def record_dispute_decision(
    db: Session,
    claim: DisputeResolutionClaim,
    *,
    outcome: str,
    basis: str,
    authority: str,
    decided_by_admin_id: int | None = None,
    reason_code: str = "",
    reason_category: str = "OTHER_SERVICE_REASON",
    external_proceeding_id: int | None = None,
    settlement_id: int | None = None,
) -> DisputeDecision:
    """ZR-ENG-CLR-010 Section 19/22/26: append-only history row for every
    point this build actually decides a claim's outcome -- see
    models/dispute_decision.py's own docstring for why this exists
    alongside (not instead of) the claim's flattened current-value columns.
    Shared by this module's own decide_claim and, since both already import
    from here, crud/dispute_external_proceeding.py and
    crud/dispute_settlement.py."""
    decision = DisputeDecision(
        claim_id=claim.id, case_id=claim.case_id, outcome=outcome, decision_basis=basis, authority=authority,
        decided_by_admin_id=decided_by_admin_id, reason_code=reason_code, reason_category=reason_category,
        external_proceeding_id=external_proceeding_id, settlement_id=settlement_id,
    )
    db.add(decision)
    db.flush()
    return decision


def decide_claim(db: Session, claim: DisputeResolutionClaim, admin: AdminUser, data: DisputeClaimDecide) -> DisputeResolutionClaim:
    # AC-6: "Admin cannot issue an internal decision for a claim whose
    # authority class is external-only."
    if claim.authority_class != "A0":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Admin cannot issue an internal decision for a claim whose authority class is external-only",
        )
    if data.outcome not in ("UPHELD", "PARTLY_UPHELD", "NOT_UPHELD"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "outcome must be UPHELD, PARTLY_UPHELD or NOT_UPHELD")
    # QA-Q51/Section 25 "authority matrix": see
    # models/dispute_decision.py's DISPUTE_INTERNAL_DECISION_REASON_CATEGORIES
    # docstring -- this vocabulary has no legal-liability value in it at
    # all, so an admin cannot "mark legal liability using a service-level
    # resolution code" through this field by construction.
    if data.reason_category not in DISPUTE_INTERNAL_DECISION_REASON_CATEGORIES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"reason_category must be one of {DISPUTE_INTERNAL_DECISION_REASON_CATEGORIES}",
        )

    if claim.status != "INTERNAL_REVIEW":
        transition_claim(claim, "INTERNAL_REVIEW", note="internal decision path for an A0 (platform-controlled) claim")
    transition_claim(claim, data.outcome)

    claim.outcome = data.outcome
    claim.reason_code = data.reason_code
    claim.decided_at = datetime.now(timezone.utc)
    claim.decided_by_admin_id = admin.id
    db.flush()

    record_dispute_decision(
        db, claim, outcome=data.outcome, basis="ADMIN_INTERNAL_DECISION", authority="admin",
        decided_by_admin_id=admin.id, reason_code=data.reason_code, reason_category=data.reason_category,
    )

    # Section 26: "Recommended 7 calendar days to request review of a
    # Zoiko-controlled determination" -- a real, extendable record instead
    # of an invisible constant. A re-decision (after a prior review
    # request) gets its own fresh deadline; request_internal_review always
    # reads the most recently created one.
    internal_review_due_at = claim.decided_at + _INTERNAL_REVIEW_WINDOW
    db.add(DisputeDeadline(
        case_id=claim.case_id, claim_id=claim.id, deadline_type="INTERNAL_REVIEW",
        due_at=internal_review_due_at, status="PENDING", source="SYSTEM_DEFAULT",
        reminder_at=deadline_crud.compute_reminder_at(claim.decided_at, internal_review_due_at),
    ))

    _sync_case_status_after_claim_change(claim.case)
    db.commit()

    _notify_case_participants(
        db, claim.case,
        title="A dispute claim was decided",
        message=f"Claim #{claim.id} was decided: {data.outcome.replace('_', ' ').title()}.",
        notification_type="dispute_claim.decided",
    )
    db.commit()
    db.refresh(claim)
    return claim


def _requires_maker_checker(claim: DisputeResolutionClaim, amount: float) -> bool:
    """Section 20: 'Mandatory above configured thresholds or for safety/
    legal/manual override cases.' Three concrete, already-existing
    triggers -- no invented 'manual override' concept is modeled, since
    nothing in this codebase has one yet."""
    if amount >= settings.dispute_financial_hold_maker_checker_threshold:
        return True
    if claim.case.severity == "SEV-0":
        return True
    if claim.resolver_confidence == "LEGAL_REVIEW_REQUIRED":
        return True
    return False


def open_financial_hold(db: Session, claim: DisputeResolutionClaim, admin: AdminUser, data: DisputeFinancialHoldCreate) -> DisputeResolutionHold:
    if not data.authority_basis.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "authority_basis is required for a financial hold")
    if data.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount must be positive")
    # QA-Q43: a hold denominated in a different currency than the claim it
    # secures can't be sanity-checked against that claim's amount by anyone
    # downstream (release, reporting, the claim's own resolution) -- reject
    # it at creation instead of persisting a silently-inconsistent pair.
    if data.currency != claim.currency:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Hold currency ({data.currency}) must match the claim's currency ({claim.currency})",
        )

    hold = DisputeResolutionHold(
        claim_id=claim.id,
        amount=data.amount,
        currency=data.currency,
        authority_basis=data.authority_basis,
        # Below the maker-checker trigger this stays today's single-step
        # behavior (straight to ACTIVE); at/above it, a different admin
        # must call approve_financial_hold before it takes effect.
        status="PROPOSED" if _requires_maker_checker(claim, data.amount) else "ACTIVE",
        reason_code=data.reason_code,
        created_by_admin_id=admin.id,
        # AC-10: a case officer's own review_at wins; otherwise a
        # reasonable MVP default window applies -- see the column's own
        # docstring in models/dispute.py for why this is never auto-enforced.
        review_at=data.review_at or (datetime.now(timezone.utc) + timedelta(days=settings.dispute_financial_hold_default_review_days)),
    )
    db.add(hold)
    db.commit()
    db.refresh(hold)
    return hold


def is_hold_overdue_for_review(hold: DisputeResolutionHold) -> bool:
    """AC-10: flags a still-open hold whose review_at has passed -- a
    signal for a human to look at it, never an automatic release (Section
    20's own "It is not a finding of liability" / minimum-necessary-
    restraint principle means only an authorized admin action, never a
    timer, ever changes a hold's status)."""
    if hold.status not in ("PROPOSED", "ACTIVE", "RELEASE_PENDING"):
        return False
    if hold.review_at is None:
        return False
    return datetime.now(timezone.utc) > hold.review_at


def _commit_hold_or_stale_conflict(db: Session, hold: DisputeResolutionHold) -> None:
    """AC-39/QA-Q42: converts a concurrent-modification StaleDataError
    (raised by DisputeResolutionHold's version_id_col on commit) into a
    clean 409 instead of an unhandled ORM error surfacing as a 500."""
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This financial hold was modified by another admin in the meantime -- reload and retry",
        )
    db.refresh(hold)


def approve_financial_hold(db: Session, hold: DisputeResolutionHold, admin: AdminUser) -> DisputeResolutionHold:
    if hold.status != "PROPOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This financial hold is not awaiting approval (currently {hold.status})")
    if admin.id == hold.created_by_admin_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "The admin who opened this financial hold cannot also approve it")

    hold.status = "ACTIVE"
    hold.approved_by_admin_id = admin.id
    hold.approved_at = datetime.now(timezone.utc)
    _commit_hold_or_stale_conflict(db, hold)
    return hold


def release_financial_hold(db: Session, hold: DisputeResolutionHold, admin: AdminUser, data: DisputeFinancialHoldRelease) -> DisputeResolutionHold:
    if hold.status not in ("PROPOSED", "ACTIVE"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This financial hold has already been released or closed")

    if _requires_maker_checker(hold.claim, hold.amount):
        hold.status = "RELEASE_PENDING"
        hold.release_requested_by_admin_id = admin.id
        hold.release_requested_at = datetime.now(timezone.utc)
        hold.release_reason = data.release_reason
        _commit_hold_or_stale_conflict(db, hold)
        return hold

    hold.status = "RELEASED"
    hold.released_at = datetime.now(timezone.utc)
    hold.release_reason = data.release_reason
    _commit_hold_or_stale_conflict(db, hold)
    return hold


def confirm_release_financial_hold(db: Session, hold: DisputeResolutionHold, admin: AdminUser) -> DisputeResolutionHold:
    if hold.status != "RELEASE_PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This financial hold is not awaiting release confirmation (currently {hold.status})")
    if admin.id == hold.release_requested_by_admin_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "The admin who requested this release cannot also confirm it")

    hold.status = "RELEASED"
    hold.released_at = datetime.now(timezone.utc)
    _commit_hold_or_stale_conflict(db, hold)
    return hold


def _has_open_external_proceeding(db: Session, claim_id: int) -> bool:
    proceeding_ids = db.scalars(
        select(DisputeExternalProceedingClaimLink.proceeding_id).where(DisputeExternalProceedingClaimLink.claim_id == claim_id)
    )
    for proceeding_id in proceeding_ids:
        proceeding = db.get(DisputeExternalProceeding, proceeding_id)
        if proceeding and proceeding.status not in EXTERNAL_PROCEEDING_TERMINAL_STATUSES:
            return True
    return False


def close_case(db: Session, case: DisputeResolutionCase, admin: AdminUser, *, force_close_reason: str = "") -> DisputeResolutionCase:
    # AC-24: a case cannot close while a required material claim remains
    # unresolved -- UNLESS force_close_reason is given (QA-Q45: partial
    # closure while a claim is genuinely stuck awaiting an external
    # scheme/tribunal with no ETA), and even then only when every
    # non-terminal claim is actually externally referred with an open
    # proceeding -- never for a claim that's simply still being worked
    # internally.
    non_terminal = [c for c in case.claims if c.status not in CLAIM_TERMINAL_STATUSES]
    force_closing = False
    if non_terminal:
        if not force_close_reason.strip():
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Cannot close case #{case.id}: {len(non_terminal)} claim(s) are not yet resolved",
            )
        for claim in non_terminal:
            if claim.status != "EXTERNAL_REFERRAL" or not _has_open_external_proceeding(db, claim.id):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"Cannot force-close case #{case.id}: claim {claim.id} is not awaiting an open external proceeding",
                )
        force_closing = True

    # AC-52: case closure blocked while an active financial hold remains --
    # RELEASE_PENDING is still unresolved (a release requested but not yet
    # confirmed by a second admin is not the same as actually released).
    active_holds = [hold for c in case.claims for hold in c.holds if hold.status in ("PROPOSED", "ACTIVE", "RELEASE_PENDING")]
    if active_holds:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot close case: an unresolved financial hold remains open")

    if force_closing:
        case.partial_closure_reason = force_close_reason.strip()
        transition_case(case, "CLOSED")
    else:
        if case.status != "RESOLVED":
            transition_case(case, "RESOLVED")
        transition_case(case, "CLOSED")
    case.closed_at = datetime.now(timezone.utc)
    case.closed_by_admin_id = admin.id

    db.commit()
    _notify_case_participants(
        db, case, title="Your dispute case was closed",
        message=f"Dispute case #{case.id} has been closed.", notification_type="dispute_case.closed",
    )
    db.commit()
    db.refresh(case)
    return case


# ZR-ENG-CLR-010 Section 26: 7 calendar days, same "reasonable MVP default,
# not a verified legal figure" honesty as booking_change_request.py's own
# fixed 7-day BCR expiry window -- no per-jurisdiction review-window field
# exists yet.
_INTERNAL_REVIEW_WINDOW = timedelta(days=7)

# AC-29/Section 26: "recommended commercial default 5 business days only
# where no statutory/forum rule supersedes it" -- the fallback used when a
# claim has no resolvable market_policy_pack at all (a pure ZOIKO_SERVICE
# case with no occupancy, or an unconfigured jurisdiction); when one IS
# resolvable, MarketPolicyPack.dispute_response_window_days is the actual
# forum-pack-computed value AC-29 asks for.
_DEFAULT_PARTY_RESPONSE_WINDOW_DAYS = 5


def reopen_case(
    db: Session, case: DisputeResolutionCase, admin: AdminUser, *, grounds: str, note: str = "", claim_ids: list[int] | None = None,
) -> DisputeResolutionCase:
    """AC-31: 'A closed case can be reopened without deleting or altering
    its prior closure event.' case.closed_at/closed_by_admin_id are never
    touched here -- reopened_at/reopened_by_admin_id/reopen_grounds/
    reopen_note are the separate, additive reopen event Section 26 asks
    for. Admin-only, broad authority: unlike request_internal_review below,
    any claim (including a voluntary SETTLED one) can be named in
    claim_ids, per Section 26's own "fraud finding" ground."""
    if case.status not in ("RESOLVED", "CLOSED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a RESOLVED or CLOSED case can be reopened (currently {case.status})")
    if grounds not in DISPUTE_CASE_REOPEN_GROUNDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown reopen grounds '{grounds}'")

    transition_case(case, "REOPENED")
    transition_case(case, "IN_PROGRESS")
    case.reopened_at = datetime.now(timezone.utc)
    case.reopened_by_admin_id = admin.id
    case.reopen_grounds = grounds
    case.reopen_note = note

    for claim_id in claim_ids or []:
        claim = db.get(DisputeResolutionClaim, claim_id)
        if not claim or claim.case_id != case.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Claim {claim_id} does not belong to this case")
        if claim.status not in CLAIM_TERMINAL_STATUSES:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Claim {claim_id} is not in a resolved state (currently {claim.status})")
        transition_claim(claim, "EVIDENCE", note="case reopened")

    db.commit()
    _notify_case_participants(
        db, case, title="Your dispute case was reopened",
        message=f"Dispute case #{case.id} has been reopened ({grounds.replace('_', ' ').lower()}).",
        notification_type="dispute_case.reopened",
    )
    db.commit()
    db.refresh(case)
    return case


def request_internal_review(
    db: Session, case: DisputeResolutionCase, claim: DisputeResolutionClaim, *, reason: str = "",
) -> DisputeResolutionClaim:
    """AC-30/34/35: a party asking Zoiko to reconsider its own (A0)
    determination -- separate from external appeal rights, and a late
    request is refused outright (409), never silently accepted past the
    window. Reuses decide_claim's existing INTERNAL_REVIEW handling
    unchanged for the actual re-decision -- this function only gets the
    claim (and, if necessary, the case) back into a state decide_claim
    already knows how to act on."""
    if claim.case_id != case.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This claim does not belong to this case")
    if claim.authority_class != "A0":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a Zoiko-controlled (A0) determination can be internally reviewed")
    if claim.status not in ("UPHELD", "PARTLY_UPHELD", "NOT_UPHELD"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This claim is not in a decided state eligible for review (currently {claim.status})")
    if claim.decided_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This claim has no recorded decision to review")

    # decide_claim (Phase 1) creates a real, admin-extendable INTERNAL_REVIEW
    # deadline for every A0 decision -- that row is authoritative when it
    # exists. Fall back to the fixed 7-day window only for a claim decided
    # before this deadline tracking existed.
    deadline = deadline_crud.latest_deadline_for_claim(db, claim.id, deadline_type="INTERNAL_REVIEW")
    if deadline is not None:
        if datetime.now(timezone.utc) > deadline.due_at:
            raise HTTPException(status.HTTP_409_CONFLICT, "The internal review window for this determination has passed")
    elif datetime.now(timezone.utc) - claim.decided_at > _INTERNAL_REVIEW_WINDOW:
        raise HTTPException(status.HTTP_409_CONFLICT, "The 7-day internal review window for this determination has passed")

    transition_claim(claim, "INTERNAL_REVIEW", note="internal review requested")
    if reason:
        claim.reason_code = reason
    if deadline is not None and deadline.status != "CANCELLED":
        deadline.status = "MET"

    if case.status in ("RESOLVED", "CLOSED"):
        transition_case(case, "REOPENED")
        transition_case(case, "IN_PROGRESS")
        case.reopened_at = datetime.now(timezone.utc)
        case.reopened_by_admin_id = None
        case.reopen_grounds = "INTERNAL_REVIEW_REQUESTED"
        case.reopen_note = reason

    db.commit()
    db.refresh(claim)
    return claim
