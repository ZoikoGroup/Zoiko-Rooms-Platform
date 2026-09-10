from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.core.receipt_documents import resolve_receipt_document_path
from app.crud.finance import annotate_payment_context, get_or_create_payment_receipt, get_payment_or_404
from app.crud.guest import get_guest_for_user
from app.db.session import get_db
from app.models.finance import SimulatedPayment
from app.models.user_account import UserAccount
from app.schemas.finance import SimulatedPaymentRead

router = APIRouter(prefix="/api/users/payments", tags=["user-payments"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[SimulatedPaymentRead])
def list_user_payment_history(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read-only payment history limited to the authenticated user's guest record."""
    guest = get_guest_for_user(db, user)
    if not guest:
        return []

    payments = list(
        db.scalars(
            select(SimulatedPayment)
            .options(selectinload(SimulatedPayment.allocations))
            .where(SimulatedPayment.guest_id == guest.id)
            .order_by(SimulatedPayment.created_at.desc())
        )
    )
    return [SimulatedPaymentRead.model_validate(annotate_payment_context(p)) for p in payments]


@router.get("/{payment_id}/receipt")
def download_own_payment_receipt(
    payment_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-005 Section 13.1/AC-25: a renter can download the receipt
    for their own successful payment -- same ownership-check shape as
    list_user_payment_history above (guest_id, not the payment's admin/party
    chain, since a payer's receipt is theirs regardless of who processed it)."""
    guest = get_guest_for_user(db, user)
    payment = get_payment_or_404(db, payment_id)
    if not guest or guest.id != payment.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This payment does not belong to you")
    if payment.status != "SUCCEEDED":
        raise HTTPException(status.HTTP_409_CONFLICT, "No receipt exists for a payment that hasn't succeeded")

    receipt = get_or_create_payment_receipt(db, payment)
    db.commit()

    pdf_bytes = resolve_receipt_document_path(receipt.storage_ref).read_bytes()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{receipt.receipt_number}.pdf"'},
    )
