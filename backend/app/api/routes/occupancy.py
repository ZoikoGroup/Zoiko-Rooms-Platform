from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud import finance as finance_crud
from app.crud import activation_gate as gate_crud
from app.crud import leasing as leasing_crud
from app.crud import occupancy as crud
from app.crud import sublet as sublet_crud
from app.crud.audit import log_audit_event
from app.crud.eligibility import check_move_in_eligibility
from app.crud.events import emit_event
from app.crud.party import assert_provider_access, party_id_for_listing
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.occupancy import Occupancy
from app.models.occupancy_activation import OccupancyActivationDecision, OccupancyHandoverEvent
from app.schemas.activation_gate import (
    ActivationDecisionRead, ActivationGateStatusRead, HandoverEventCreate,
    HandoverEventRead, OccupancyTimelineRead,
)
from app.schemas.finance import ObligationRead
from app.schemas.occupancy import OccupancyRead
from app.schemas.leasing import SubletRequestDecision, SubletRequestRead

router = APIRouter(prefix="/api/occupancy", tags=["occupancy"], dependencies=[Depends(get_current_admin)])


def _occupancy_for_provider(db: Session, occupancy_id: int, admin: AdminUser) -> Occupancy:
    occupancy = crud.get_occupancy_or_404(db, occupancy_id)
    assert_provider_access(db, admin, party_id_for_listing(occupancy.listing))
    return occupancy


@router.get("/agreements/{agreement_id}/move-in-eligibility")
def get_move_in_eligibility(agreement_id: int, db: Session = Depends(get_db)):
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id)
    reasons = check_move_in_eligibility(db, agreement)
    return {"eligible": not reasons, "reasons": reasons}


@router.post("/agreements/{agreement_id}/confirm-move-in", response_model=OccupancyRead)
def post_confirm_move_in(
    agreement_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    agreement = leasing_crud.get_agreement_or_404(db, agreement_id)
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending occupancy found for this agreement")
    if occupancy.status == "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Occupancy is already active")
    if occupancy.status == "ENDED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Occupancy has already ended and cannot be reactivated")
    if occupancy.status != "PENDING_MOVE_IN":
        raise HTTPException(status.HTTP_409_CONFLICT, "Occupancy is not awaiting move-in")

    correlation_id = get_correlation_id(request)
    evaluation = gate_crud.evaluate_activation_gate(db, occupancy)
    decision = gate_crud.persist_activation_decision(
        db, occupancy, evaluation, trigger="confirm_move_in", admin=admin, correlation_id=correlation_id,
    )
    log_audit_event(db, admin, "occupancy.activation_evaluated", "occupancy", str(occupancy.id), correlation_id, reason=evaluation.outcome)
    emit_event(db, "occupancy.activation_evaluated", "occupancy", str(occupancy.id), {"decisionId": decision.id, "outcome": evaluation.outcome})
    if evaluation.outcome == "BLOCKED":
        emit_event(db, "occupancy.activation_blocked", "occupancy", str(occupancy.id), {"decisionId": decision.id, "reasonCodes": evaluation.reason_codes})
    if evaluation.outcome != "ACTIVATE":
        db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "Activation gate did not permit move-in", "outcome": evaluation.outcome,
             "reasonCodes": evaluation.reason_codes, "decisionId": decision.id},
        )

    occupancy = crud.confirm_move_in(db, agreement, admin)
    log_audit_event(db, admin, "occupancy.move_in", "occupancy", str(occupancy.id), correlation_id)
    emit_event(db, "occupancy.active", "occupancy", str(occupancy.id), {"roomId": occupancy.room_id})
    db.commit()
    return crud.to_occupancy_read(occupancy)


@router.post("/{occupancy_id}/handover/prepare", response_model=HandoverEventRead)
def post_prepare_handover(
    occupancy_id: int, payload: HandoverEventCreate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    occupancy = _occupancy_for_provider(db, occupancy_id, admin)
    event = crud.record_handover_event(
        db, occupancy, event_type="HANDOVER_READY", actor_kind="provider_admin", actor_admin_id=admin.id,
        evidence_ref=payload.evidence_ref, notes=payload.notes, correlation_id=get_correlation_id(request),
    )
    log_audit_event(db, admin, "occupancy.handover_ready", "occupancy", str(occupancy_id), get_correlation_id(request))
    emit_event(db, "occupancy.handover_ready", "occupancy", str(occupancy_id), {"handoverEventId": event.id})
    db.commit()
    return event


@router.post("/{occupancy_id}/handover/events", response_model=HandoverEventRead)
def post_possession_delivered(
    occupancy_id: int, payload: HandoverEventCreate, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    occupancy = _occupancy_for_provider(db, occupancy_id, admin)
    event = crud.record_handover_event(
        db, occupancy, event_type="POSSESSION_DELIVERED", actor_kind="provider_admin", actor_admin_id=admin.id,
        evidence_ref=payload.evidence_ref, notes=payload.notes, correlation_id=get_correlation_id(request),
    )
    log_audit_event(db, admin, "occupancy.possession_delivered", "occupancy", str(occupancy_id), get_correlation_id(request))
    emit_event(db, "occupancy.possession_delivered", "occupancy", str(occupancy_id), {"handoverEventId": event.id})
    db.commit()
    return event


@router.post("/{occupancy_id}/activation-gate/evaluate", response_model=ActivationDecisionRead)
def post_evaluate_activation_gate(
    occupancy_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    occupancy = _occupancy_for_provider(db, occupancy_id, admin)
    evaluation = gate_crud.evaluate_activation_gate(db, occupancy)
    decision = gate_crud.persist_activation_decision(
        db, occupancy, evaluation, trigger="explicit_evaluation", admin=admin, correlation_id=get_correlation_id(request),
    )
    log_audit_event(db, admin, "occupancy.activation_evaluated", "occupancy", str(occupancy_id), get_correlation_id(request), reason=evaluation.outcome)
    emit_event(db, "occupancy.activation_evaluated", "occupancy", str(occupancy_id), {"decisionId": decision.id, "outcome": evaluation.outcome})
    if evaluation.outcome == "BLOCKED":
        emit_event(db, "occupancy.activation_blocked", "occupancy", str(occupancy_id), {"decisionId": decision.id, "reasonCodes": evaluation.reason_codes})
    db.commit()
    return decision


@router.get("/{occupancy_id}/activation-gate", response_model=ActivationGateStatusRead)
def get_activation_gate_status(occupancy_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    _occupancy_for_provider(db, occupancy_id, admin)
    decisions = gate_crud.activation_decisions_for(db, occupancy_id)
    return ActivationGateStatusRead(occupancy_id=occupancy_id, latest_decision=decisions[-1] if decisions else None,
                                    handover_events=gate_crud.handover_events_for(db, occupancy_id))


@router.get("/{occupancy_id}/timeline", response_model=OccupancyTimelineRead)
def get_occupancy_timeline(occupancy_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    _occupancy_for_provider(db, occupancy_id, admin)
    return OccupancyTimelineRead(handover_events=gate_crud.handover_events_for(db, occupancy_id),
                                 activation_decisions=gate_crud.activation_decisions_for(db, occupancy_id))


@router.get("", response_model=list[OccupancyRead])
def get_occupancies(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [crud.to_occupancy_read(o) for o in crud.list_occupancies_for(db, admin)]


@router.get("/rent-due-check", response_model=list[OccupancyRead])
def get_rent_due_check(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [crud.to_occupancy_read(o) for o in crud.list_occupancies_missing_upcoming_rent(db, admin)]


@router.post("/{occupancy_id}/generate-rent", response_model=ObligationRead | None)
def post_generate_rent(
    occupancy_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    occupancy = crud.get_occupancy_or_404(db, occupancy_id)
    obligation = crud.generate_next_rent_obligation(db, occupancy, admin)
    if obligation:
        log_audit_event(db, admin, "occupancy.generate_rent", "occupancy", str(occupancy_id), get_correlation_id(request))
        emit_event(db, "obligation.created", "obligation", str(obligation.id), {"obligationType": "RENT", "occupancyId": occupancy_id})
        db.commit()
        return finance_crud.to_obligation_read(obligation)
    return None


@router.post("/{occupancy_id}/end", response_model=OccupancyRead)
def post_end_occupancy(
    occupancy_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    occupancy = crud.get_occupancy_or_404(db, occupancy_id)
    updated = crud.end_occupancy(db, occupancy, admin)
    log_audit_event(db, admin, "occupancy.end", "occupancy", str(occupancy_id), get_correlation_id(request))
    emit_event(db, "occupancy.ended", "occupancy", str(occupancy_id), {})
    db.commit()
    return crud.to_occupancy_read(updated)


@router.get("/sublet-requests", response_model=list[SubletRequestRead], dependencies=[Depends(require_super_admin)])
def list_pending_sublet_requests(
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """List all pending sublet requests for super admin review."""
    sublet_requests = sublet_crud.list_pending_sublet_requests(db, admin)
    return [sublet_crud.to_sublet_request_read(db, sr) for sr in sublet_requests]


@router.post("/sublet-requests/{sublet_request_id}/approve", response_model=SubletRequestRead, dependencies=[Depends(require_super_admin)])
def approve_sublet_request(
    sublet_request_id: int,
    request: Request,
    payload: SubletRequestDecision | None = None,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Super admin approves a sublet request."""
    sublet_request = sublet_crud.get_sublet_request(db, sublet_request_id)
    if not sublet_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")

    approved = sublet_crud.approve_sublet_request(db, sublet_request, admin, payload.notes if payload else "")
    log_audit_event(db, admin, "sublet_request.approve", "sublet_request", str(sublet_request_id), get_correlation_id(request))
    emit_event(
        db, "sublet_request.approved", "sublet_request", str(sublet_request_id),
        {"occupancyId": approved.current_occupancy_id, "proposedRenterPartyId": approved.proposed_renter_party_id},
    )
    db.commit()

    return sublet_crud.to_sublet_request_read(db, approved)


@router.post("/sublet-requests/{sublet_request_id}/reject", response_model=SubletRequestRead, dependencies=[Depends(require_super_admin)])
def reject_sublet_request(
    sublet_request_id: int,
    request: Request,
    payload: SubletRequestDecision | None = None,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Super admin rejects a sublet request."""
    sublet_request = sublet_crud.get_sublet_request(db, sublet_request_id)
    if not sublet_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")

    rejected = sublet_crud.reject_sublet_request(db, sublet_request, admin, payload.notes if payload else "")
    log_audit_event(db, admin, "sublet_request.reject", "sublet_request", str(sublet_request_id), get_correlation_id(request))
    emit_event(db, "sublet_request.rejected", "sublet_request", str(sublet_request_id), {"occupancyId": rejected.current_occupancy_id})
    db.commit()

    return sublet_crud.to_sublet_request_read(db, rejected)
