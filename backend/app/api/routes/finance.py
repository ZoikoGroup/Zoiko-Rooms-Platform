from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud import finance as crud
from app.crud.finance import annotate_payment_context
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.party import Party
from app.schemas.finance import (
    DepositRecordRead,
    DepositRelease,
    DisputeCreate,
    DisputeRead,
    DisputeResolve,
    ObligationRead,
    PaymentConfirm,
    PayoutRecordRead,
    PayoutRunRequest,
    ReconciliationRunRead,
    RefundDecide,
    RefundRequestCreate,
    RefundRequestRead,
    SimulatedPaymentCreate,
    SimulatedPaymentRead,
)

router = APIRouter(prefix="/api/finance", tags=["finance"], dependencies=[Depends(get_current_admin)])


@router.get("/obligations", response_model=list[ObligationRead])
def get_obligations(
    occupancy_id: int | None = None,
    agreement_id: int | None = None,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return [crud.to_obligation_read(o) for o in crud.list_obligations(db, admin, occupancy_id, agreement_id)]


@router.get("/payments", response_model=list[SimulatedPaymentRead])
def get_payments(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.list_payments(db, admin)


@router.post("/payments", response_model=SimulatedPaymentRead, status_code=status.HTTP_201_CREATED)
def post_create_payment(payload: SimulatedPaymentCreate, db: Session = Depends(get_db)):
    return annotate_payment_context(crud.create_payment_intent(db, payload))


@router.post("/payments/{payment_id}/confirm", response_model=SimulatedPaymentRead)
def post_confirm_payment(
    payment_id: int,
    payload: PaymentConfirm,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    payment = crud.get_payment_or_404(db, payment_id)
    before_state = payment.status
    updated = crud.confirm_payment(db, payment, payload, admin)
    if updated.status == "SUCCEEDED":
        log_audit_event(
            db, admin, "payment.confirm", "simulated_payment", str(payment_id), get_correlation_id(request),
            before_state=before_state, after_state=updated.status,
        )
        emit_event(
            db,
            "payment.succeeded",
            "simulated_payment",
            str(payment_id),
            {"amount": float(updated.amount), "currency": updated.currency},
        )
        db.commit()
    return annotate_payment_context(updated)


@router.get("/deposits", response_model=list[DepositRecordRead])
def get_deposits(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.list_deposit_records(db, admin)


@router.post("/deposits/{deposit_id}/release", response_model=DepositRecordRead)
def post_release_deposit(
    deposit_id: int,
    payload: DepositRelease,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    record = crud.get_deposit_record_or_404(db, deposit_id)
    updated = crud.release_deposit(db, record, admin, payload)
    log_audit_event(db, admin, "deposit.release", "deposit_record", str(deposit_id), get_correlation_id(request))
    emit_event(db, "deposit.released", "deposit_record", str(deposit_id), {"amount": payload.amount, "moneyPlane": "SAFEGUARDED"})
    db.commit()
    return updated


@router.post("/deposits/{deposit_id}/forfeit", response_model=DepositRecordRead)
def post_forfeit_deposit(
    deposit_id: int,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    record = crud.get_deposit_record_or_404(db, deposit_id)
    updated = crud.forfeit_deposit(db, record, admin)
    log_audit_event(db, admin, "deposit.forfeit", "deposit_record", str(deposit_id), get_correlation_id(request))
    emit_event(db, "deposit.forfeited", "deposit_record", str(deposit_id), {"moneyPlane": "SAFEGUARDED"})
    db.commit()
    return updated


@router.post("/payouts/run", response_model=PayoutRecordRead)
def post_run_payout(
    payload: PayoutRunRequest,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    party = db.get(Party, payload.party_id)
    if not party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")
    payout = crud.run_payout(db, party, admin, payload.period_key)
    log_audit_event(db, admin, "payout.run", "payout_record", str(payout.id), get_correlation_id(request), reason=payout.status)
    emit_event(
        db,
        "payout.paid" if payout.status == "PAID" else "payout.held",
        "payout_record",
        str(payout.id),
        {"amount": float(payout.amount), "currency": payout.currency, "moneyPlane": "REVENUE", "partyId": party.id},
    )
    db.commit()
    return payout


@router.get("/payouts", response_model=list[PayoutRecordRead])
def get_payouts(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.list_payouts_for(db, admin)


@router.post("/refunds", response_model=RefundRequestRead, status_code=status.HTTP_201_CREATED)
def post_request_refund(
    payload: RefundRequestCreate,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    refund = crud.request_refund(db, payload, admin)
    log_audit_event(db, admin, "refund.request", "refund_request", str(refund.id), get_correlation_id(request))
    db.commit()
    return refund


@router.get("/refunds", response_model=list[RefundRequestRead])
def get_refunds(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.list_refund_requests(db, admin)


@router.post("/refunds/{refund_id}/decide", response_model=RefundRequestRead)
def post_decide_refund(
    refund_id: int,
    payload: RefundDecide,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    refund = crud.get_refund_or_404(db, refund_id)
    updated = crud.decide_refund(db, refund, admin, payload)
    log_audit_event(db, admin, "refund.decide", "refund_request", str(refund_id), get_correlation_id(request), reason=updated.status)
    if updated.status == "COMPLETED":
        emit_event(
            db,
            "refund.completed",
            "refund_request",
            str(refund_id),
            {"amount": float(updated.amount), "moneyPlane": updated.obligation.money_plane},
        )
    db.commit()
    return updated


@router.post("/disputes", response_model=DisputeRead, status_code=status.HTTP_201_CREATED)
def post_open_dispute(
    payload: DisputeCreate,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    dispute = crud.open_dispute(db, payload, admin)
    log_audit_event(db, admin, "dispute.open", "dispute_case", str(dispute.id), get_correlation_id(request))
    db.commit()
    return dispute


@router.get("/disputes", response_model=list[DisputeRead])
def get_disputes(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.list_dispute_cases(db, admin)


@router.post("/disputes/{dispute_id}/resolve", response_model=DisputeRead)
def post_resolve_dispute(
    dispute_id: int,
    payload: DisputeResolve,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    dispute = crud.get_dispute_or_404(db, dispute_id)
    updated = crud.resolve_dispute(db, dispute, admin, payload)
    log_audit_event(db, admin, "dispute.resolve", "dispute_case", str(dispute_id), get_correlation_id(request), reason=updated.status)
    db.commit()
    return updated


@router.post("/reconciliation/run", response_model=ReconciliationRunRead, dependencies=[Depends(require_super_admin)])
def post_run_reconciliation(
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    run = crud.run_reconciliation(db, admin)
    log_audit_event(db, admin, "reconciliation.run", "reconciliation_run", str(run.id), get_correlation_id(request), reason=run.status)
    db.commit()
    return run


@router.get("/reconciliation", response_model=list[ReconciliationRunRead], dependencies=[Depends(require_super_admin)])
def get_reconciliation_runs(db: Session = Depends(get_db)):
    return crud.list_reconciliation_runs(db)
