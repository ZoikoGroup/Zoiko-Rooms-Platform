from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.core.receipt_documents import resolve_receipt_document_path
from app.core.rent_invoice_documents import resolve_rent_invoice_document_path
from app.crud import finance as finance_crud
from app.crud.finance import (
    annotate_payment_context,
    get_obligation_or_404,
    get_or_create_payment_receipt,
    get_or_create_rent_invoice,
    get_payment_or_404,
)
from app.crud.guest import get_guest_for_user
from app.crud.occupancy import get_occupancy_or_404
from app.db.session import get_db
from app.models.finance import SimulatedPayment
from app.models.user_account import UserAccount
from app.schemas.finance import AutopayMandateCreate, AutopayMandateRead, SimulatedPaymentRead

router = APIRouter(prefix="/api/users/payments", tags=["user-payments"], dependencies=[Depends(get_current_user)])


def _get_own_guest_or_403(db: Session, user: UserAccount):
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No guest record for this account")
    return guest


@router.post("/mandates", response_model=AutopayMandateRead, status_code=status.HTTP_201_CREATED)
def create_autopay_mandate(
    payload: AutopayMandateCreate,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-005 Section 6.1-E/16.1 POST /mandates."""
    guest = _get_own_guest_or_403(db, user)
    occupancy = get_occupancy_or_404(db, payload.occupancy_id)
    mandate = finance_crud.create_autopay_mandate(db, guest, occupancy)
    return mandate


@router.get("/mandates", response_model=list[AutopayMandateRead])
def list_my_autopay_mandates(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = _get_own_guest_or_403(db, user)
    return finance_crud.list_autopay_mandates_for_guest(db, guest)


@router.delete("/mandates/{mandate_id}", response_model=AutopayMandateRead)
def revoke_autopay_mandate(
    mandate_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-005 Section 16.1 DELETE /mandates/{id}: 'Revoke future
    autopay; preserves obligations' (AC-29)."""
    guest = _get_own_guest_or_403(db, user)
    mandate = finance_crud.get_autopay_mandate_or_404(db, mandate_id)
    return finance_crud.revoke_autopay_mandate(db, mandate, guest)


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


@router.get("/obligations/{obligation_id}/rent-invoice")
def download_own_rent_invoice(
    obligation_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-005 Section 13.1: a renter can download the rent invoice for
    their own RENT obligation -- same guest_id ownership-check shape as
    download_own_payment_receipt above. Never requires the obligation to have
    been paid -- it's the request for payment, not proof one was made."""
    guest = get_guest_for_user(db, user)
    obligation = get_obligation_or_404(db, obligation_id)
    obligation_guest_id = obligation.agreement.offer.guest_id if obligation.agreement else obligation.occupancy.guest_id
    if not guest or guest.id != obligation_guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This obligation does not belong to you")
    if obligation.obligation_type != "RENT":
        raise HTTPException(status.HTTP_409_CONFLICT, "No rent invoice exists for a non-RENT obligation")

    invoice = get_or_create_rent_invoice(db, obligation)
    db.commit()

    pdf_bytes = resolve_rent_invoice_document_path(invoice.storage_ref).read_bytes()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{invoice.invoice_number}.pdf"'},
    )
