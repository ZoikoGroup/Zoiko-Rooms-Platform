from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud import finance as finance_crud
from app.crud import leasing as leasing_crud
from app.crud import occupancy as crud
from app.crud import sublet as sublet_crud
from app.crud.audit import log_audit_event
from app.crud.eligibility import check_move_in_eligibility
from app.crud.events import emit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.finance import ObligationRead
from app.schemas.occupancy import OccupancyEndRequest, OccupancyRead, TerminationRecordRead
from app.schemas.leasing import SubletRequestDecision, SubletRequestRead

router = APIRouter(prefix="/api/occupancy", tags=["occupancy"], dependencies=[Depends(get_current_admin)])


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
    occupancy = crud.confirm_move_in(db, agreement, admin)
    log_audit_event(db, admin, "occupancy.move_in", "occupancy", str(occupancy.id), get_correlation_id(request))
    emit_event(db, "occupancy.active", "occupancy", str(occupancy.id), {"roomId": occupancy.room_id})
    db.commit()
    return crud.to_occupancy_read(occupancy)


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
    payload: OccupancyEndRequest = OccupancyEndRequest(),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    occupancy = crud.get_occupancy_or_404(db, occupancy_id)
    correlation_id = get_correlation_id(request)
    updated = crud.end_occupancy(
        db, occupancy, admin, correlation_id=correlation_id,
        notice_given_at=payload.notice_given_at, liability_end_date=payload.liability_end_date,
        termination_effective_date=payload.termination_effective_date, move_out_date=payload.move_out_date,
        basis=payload.basis,
    )
    log_audit_event(db, admin, "occupancy.end", "occupancy", str(occupancy_id), correlation_id)
    emit_event(db, "occupancy.ended", "occupancy", str(occupancy_id), {})
    db.commit()
    return crud.to_occupancy_read(updated)


@router.get("/{occupancy_id}/termination-record", response_model=TerminationRecordRead)
def get_occupancy_termination_record(occupancy_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    from app.models.termination_record import TerminationRecord

    occupancy = crud.get_occupancy_or_404(db, occupancy_id)
    record = db.query(TerminationRecord).filter(TerminationRecord.occupancy_id == occupancy.id).order_by(TerminationRecord.id.desc()).first()
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No termination record for this occupancy")
    return record


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
