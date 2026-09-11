from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.core.identity_uploads import resolve_identity_document_path, save_identity_document
from app.core.payout_statement_documents import resolve_payout_statement_document_path
from app.core.service_fee_invoice_documents import resolve_service_fee_invoice_document_path
from app.core.receipt_documents import resolve_receipt_document_path
from app.crud import finance as crud
from app.crud.finance import annotate_payment_context
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.party import Party
from app.schemas.finance import (
    DepositClaimCreate,
    DepositClaimRead,
    DepositClaimResolve,
    DepositRecordRead,
    DepositRelease,
    DisputeCreate,
    DisputeRead,
    DisputeResolve,
    FinancialHoldRead,
    FinancialHoldResolve,
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


@router.get("/payments/{payment_id}/receipt")
def get_payment_receipt(
    payment_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-005 Section 13.1/AC-25. get_or_create_payment_receipt is
    idempotent -- safe to call again here even though confirm_payment already
    generates the receipt best-effort, in case that best-effort step didn't
    run for some reason."""
    receipt = crud.get_payment_receipt_for_admin(db, payment_id, admin)
    log_audit_event(db, admin, "payment_receipt.download", "payment_receipt", str(receipt.id), get_correlation_id(request))
    db.commit()

    pdf_bytes = resolve_receipt_document_path(receipt.storage_ref).read_bytes()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{receipt.receipt_number}.pdf"'},
    )


@router.get("/deposits", response_model=list[DepositRecordRead])
def get_deposits(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return [crud.to_deposit_record_read(r) for r in crud.list_deposit_records(db, admin)]


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
    return crud.to_deposit_record_read(updated)


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
    return crud.to_deposit_record_read(updated)


@router.get("/deposits/{deposit_id}/claims", response_model=list[DepositClaimRead])
def get_deposit_claims(deposit_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    record = crud.get_deposit_record_or_404(db, deposit_id)
    return [crud.to_deposit_claim_read(c) for c in crud.list_deposit_claims_for_record(db, admin, record)]


@router.post("/deposits/{deposit_id}/claims", response_model=DepositClaimRead, status_code=status.HTTP_201_CREATED)
def post_submit_deposit_claim(
    deposit_id: int,
    payload: DepositClaimCreate,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    record = crud.get_deposit_record_or_404(db, deposit_id)
    claim = crud.submit_deposit_claim(db, record, admin, payload)
    log_audit_event(db, admin, "deposit_claim.submit", "deposit_claim", str(claim.id), get_correlation_id(request))
    emit_event(db, "deposit_claim.submitted", "deposit_claim", str(claim.id), {"depositRecordId": deposit_id})
    db.commit()
    return crud.to_deposit_claim_read(claim)


@router.post("/deposit-claim-items/{item_id}/evidence", response_model=DepositClaimRead)
async def post_deposit_claim_item_evidence(
    item_id: int,
    request: Request,
    file: UploadFile = File(...),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    item = crud.get_deposit_claim_item_or_404(db, item_id)
    stored_filename, original_filename, content_type, _ = await save_identity_document(file)
    crud.attach_deposit_claim_item_evidence(db, item, admin, stored_filename, original_filename, content_type)
    log_audit_event(db, admin, "deposit_claim_item.evidence_attach", "deposit_claim_item", str(item_id), get_correlation_id(request))
    db.commit()
    return crud.to_deposit_claim_read(item.claim)


@router.get("/deposit-claim-items/{item_id}/evidence")
def get_deposit_claim_item_evidence(item_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    item = crud.get_deposit_claim_item_or_404(db, item_id)
    crud.assert_deposit_record_access(db, admin, item.claim.deposit_record)
    if not item.evidence_filename:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No evidence uploaded for this claim item")
    path = resolve_identity_document_path(item.evidence_filename)
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence file is missing")
    return FileResponse(path, media_type=item.evidence_content_type, filename=item.evidence_original_name)


@router.post("/deposit-claims/{claim_id}/resolve", response_model=DepositClaimRead)
def post_resolve_deposit_claim(
    claim_id: int,
    payload: DepositClaimResolve,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    claim = crud.get_deposit_claim_or_404(db, claim_id)
    updated = crud.resolve_deposit_claim(db, claim, admin, payload)
    log_audit_event(db, admin, "deposit_claim.resolve", "deposit_claim", str(claim_id), get_correlation_id(request), reason=payload.notes)
    emit_event(db, "deposit_claim.resolved", "deposit_claim", str(claim_id), {})
    db.commit()
    return crud.to_deposit_claim_read(updated)


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


@router.get("/payouts/{payout_id}/statement")
def get_payout_statement(
    payout_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-005 Section 6.3/13.1. get_or_create_payout_statement is
    idempotent -- safe to call again here even though run_payout already
    generates the statement best-effort, in case that best-effort step
    didn't run for some reason."""
    statement = crud.get_payout_statement_for_admin(db, payout_id, admin)
    log_audit_event(db, admin, "payout_statement.download", "payout_statement", str(statement.id), get_correlation_id(request))
    db.commit()

    pdf_bytes = resolve_payout_statement_document_path(statement.storage_ref).read_bytes()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{statement.statement_number}.pdf"'},
    )


@router.get("/payouts/{payout_id}/service-fee-invoice")
def get_service_fee_invoice(
    payout_id: int, request: Request, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-005 Section 13.1/AC-26. get_or_create_service_fee_invoice is
    idempotent -- safe to call again here even though run_payout already
    generates the invoice best-effort, in case that best-effort step didn't
    run for some reason."""
    invoice = crud.get_service_fee_invoice_for_admin(db, payout_id, admin)
    log_audit_event(db, admin, "service_fee_invoice.download", "service_fee_invoice", str(invoice.id), get_correlation_id(request))
    db.commit()

    pdf_bytes = resolve_service_fee_invoice_document_path(invoice.storage_ref).read_bytes()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{invoice.invoice_number}.pdf"'},
    )


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


@router.get("/financial-holds", response_model=list[FinancialHoldRead], dependencies=[Depends(require_super_admin)])
def get_financial_holds(status: str | None = None, db: Session = Depends(get_db)):
    return crud.list_financial_holds(db, status=status)


@router.post(
    "/financial-holds/{hold_id}/resolve", response_model=FinancialHoldRead, dependencies=[Depends(require_super_admin)],
)
def post_resolve_financial_hold(
    hold_id: int,
    payload: FinancialHoldResolve,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    hold = crud.get_financial_hold_or_404(db, hold_id)
    updated = crud.resolve_financial_hold(db, hold, admin, payload)
    log_audit_event(db, admin, "financial_hold.resolve", "financial_hold", str(hold_id), get_correlation_id(request), reason=updated.status)
    db.commit()
    return updated
