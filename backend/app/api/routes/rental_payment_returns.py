"""Deposit returns and cancellation returns paid host -> renter directly
(crud/rental_payment_return.py). Kept apart from api/routes/rental_payments.py:
these are records of money the *host* sent back -- Zoiko never issues or
holds any of it."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.correlation import get_correlation_id
from app.crud import rental_payment_return as returns_crud
from app.crud.guest import get_guest_for_user
from app.db.session import get_db
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.user_account import UserAccount
from app.schemas.rental_payment_return import (
    RentalPaymentReturnCandidateRead,
    RentalPaymentReturnCreate,
    RentalPaymentReturnDisputeRequest,
    RentalPaymentReturnRead,
)

router = APIRouter(prefix="/api/users/rental-payments", tags=["user-rental-payment-returns"], dependencies=[Depends(get_current_user)])


def _own_guest(db: Session, user: UserAccount):
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No guest record for this account")
    return guest


def _own_party(db: Session, user: UserAccount) -> Party:
    party = db.get(Party, user.party_id) if user.party_id else None
    if not party:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    return party


# --- renter -------------------------------------------------------------

@router.get("/returns", response_model=list[RentalPaymentReturnRead])
def get_my_returns(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Money your host has recorded sending back to you."""
    return returns_crud.list_returns_for_tenant(db, _own_guest(db, user).id)


@router.post("/returns/{return_id}/confirm", response_model=RentalPaymentReturnRead)
def post_confirm_return(return_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    record = returns_crud.get_return_or_404(db, return_id)
    return returns_crud.tenant_confirm_return(db, _own_guest(db, user), record, correlation_id=get_correlation_id(request))


@router.post("/returns/{return_id}/dispute", response_model=RentalPaymentReturnRead)
def post_dispute_return(
    return_id: int, payload: RentalPaymentReturnDisputeRequest, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    record = returns_crud.get_return_or_404(db, return_id)
    return returns_crud.tenant_dispute_return(
        db, _own_guest(db, user), record, details=payload.details, correlation_id=get_correlation_id(request),
    )


# --- host ---------------------------------------------------------------

@router.get("/recipient/returns", response_model=list[RentalPaymentReturnRead])
def get_recipient_returns(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Everything you've recorded sending back, newest first."""
    return returns_crud.list_returns_for_recipient(db, _own_party(db, user).id)


@router.get("/recipient/returns/candidates", response_model=list[RentalPaymentReturnCandidateRead])
def get_recipient_return_candidates(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Your ended and cancelled bookings where you received money that may
    need sending back (a deposit, or payments for a cancelled booking)."""
    return returns_crud.list_return_candidates_for_recipient(db, _own_party(db, user).id)


@router.post(
    "/recipient/occupancies/{occupancy_id}/returns", response_model=RentalPaymentReturnRead,
    status_code=status.HTTP_201_CREATED,
)
def post_record_return(
    occupancy_id: int, payload: RentalPaymentReturnCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Record money you sent back to the renter directly -- the renter is
    asked to confirm it arrived."""
    occupancy = db.get(Occupancy, occupancy_id)
    if occupancy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    return returns_crud.record_return(
        db, _own_party(db, user), occupancy, kind=payload.kind, amount=payload.amount,
        deductions_amount=payload.deductions_amount, deductions_reason=payload.deductions_reason,
        payment_method_category=payload.payment_method_category, external_reference=payload.external_reference,
        returned_date=payload.returned_date, note=payload.note, correlation_id=get_correlation_id(request),
    )
