"""The host's own deposit claims -- claim against a deposit after a tenancy,
attach evidence, lower a disputed item, record the deposit scheme's decision,
or settle the deposit early. See crud/deposit_party_flow.py for the rules."""

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.correlation import get_correlation_id
from app.core.identity_uploads import read_identity_document, save_identity_document
from app.crud import deposit_party_flow
from app.crud import finance as finance_crud
from app.crud.audit import log_audit_event
from app.models.finance import DepositRecord
from app.models.user_account import UserAccount
from app.schemas.finance import (
    DepositClaimCreate,
    DepositClaimItemRevise,
    DepositClaimRead,
    DepositRecordRead,
    DepositSchemeDecisionCreate,
)

router = APIRouter(prefix="/api/users/hosting", tags=["host-deposits"], dependencies=[Depends(get_current_user)])


def _audit(db: Session, request: Request, user: UserAccount, action: str, resource_type: str, resource_id) -> None:
    log_audit_event(db, None, action, resource_type, str(resource_id), get_correlation_id(request), reason=f"host_user:{user.id}")


def _own_record_or_404(db: Session, deposit_id: int, user: UserAccount) -> DepositRecord:
    record = db.get(DepositRecord, deposit_id)
    if record is None or not user.party_id or finance_crud._deposit_record_party_id(record) != user.party_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deposit not found")
    return record


@router.get("/deposits", response_model=list[DepositRecordRead])
def list_host_deposits(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.party_id:
        return []
    records = db.scalars(select(DepositRecord).order_by(DepositRecord.id.desc())).all()
    return [
        finance_crud.to_deposit_record_read(r) for r in records
        if finance_crud._deposit_record_party_id(r) == user.party_id
    ]


@router.get("/deposits/{deposit_id}/claims", response_model=list[DepositClaimRead])
def list_host_deposit_claims(deposit_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    record = _own_record_or_404(db, deposit_id, user)
    return [finance_crud.to_deposit_claim_read(c) for c in record.claims]


@router.post("/deposits/{deposit_id}/claims", response_model=DepositClaimRead, status_code=status.HTTP_201_CREATED)
def submit_host_deposit_claim(
    deposit_id: int, payload: DepositClaimCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    record = _own_record_or_404(db, deposit_id, user)
    claim = finance_crud.submit_deposit_claim(db, record, user, payload)
    _audit(db, request, user, "host_deposit_claim.submit", "deposit_claim", claim.id)
    db.commit()
    return finance_crud.to_deposit_claim_read(claim)


@router.post("/deposit-claim-items/{item_id}/evidence", response_model=DepositClaimRead)
async def attach_host_deposit_claim_evidence(
    item_id: int, request: Request, file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    item = finance_crud.get_deposit_claim_item_or_404(db, item_id)
    _own_record_or_404(db, item.claim.deposit_record_id, user)
    stored_filename, original_filename, content_type, _, _ = await save_identity_document(db, file)
    finance_crud.attach_deposit_claim_item_evidence(db, item, user, stored_filename, original_filename, content_type)
    _audit(db, request, user, "host_deposit_claim_item.evidence_attach", "deposit_claim_item", item_id)
    db.commit()
    return finance_crud.to_deposit_claim_read(item.claim)


@router.get("/deposit-claim-items/{item_id}/evidence")
def get_host_deposit_claim_evidence(item_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    item = finance_crud.get_deposit_claim_item_or_404(db, item_id)
    _own_record_or_404(db, item.claim.deposit_record_id, user)
    if not item.evidence_filename:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No evidence uploaded for this claim item")
    data = read_identity_document(db, item.evidence_filename)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence file is missing")
    return Response(
        content=data, media_type=item.evidence_content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{item.evidence_original_name or "evidence"}"'},
    )


@router.post("/deposit-claim-items/{item_id}/revise", response_model=DepositClaimRead)
def revise_host_deposit_claim_item(
    item_id: int, payload: DepositClaimItemRevise, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    item = finance_crud.get_deposit_claim_item_or_404(db, item_id)
    _own_record_or_404(db, item.claim.deposit_record_id, user)
    claim = deposit_party_flow.revise_item(db, item, user, payload.amount)
    _audit(db, request, user, "host_deposit_claim_item.revise", "deposit_claim_item", item_id)
    db.commit()
    return finance_crud.to_deposit_claim_read(claim)


@router.post("/deposit-claims/{claim_id}/scheme-decision", response_model=DepositClaimRead)
def record_host_deposit_scheme_decision(
    claim_id: int, payload: DepositSchemeDecisionCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    claim = finance_crud.get_deposit_claim_or_404(db, claim_id)
    _own_record_or_404(db, claim.deposit_record_id, user)
    updated = deposit_party_flow.record_scheme_decision(
        db, claim, role="HOST", user=user, item_final_amounts=payload.item_final_amounts,
        scheme_reference=payload.scheme_reference, notes=payload.notes,
    )
    _audit(db, request, user, "host_deposit_claim.scheme_decision", "deposit_claim", claim_id)
    db.commit()
    return finance_crud.to_deposit_claim_read(updated)


@router.post("/deposits/{deposit_id}/settle", response_model=DepositRecordRead)
def settle_host_deposit_now(
    deposit_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Give up any further claims and settle the deposit straight away."""
    record = _own_record_or_404(db, deposit_id, user)
    updated = deposit_party_flow.host_settle_now(db, record, user)
    _audit(db, request, user, "host_deposit.settle", "deposit_record", deposit_id)
    db.commit()
    return finance_crud.to_deposit_record_read(updated)
