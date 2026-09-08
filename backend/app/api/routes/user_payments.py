from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user
from app.crud import finance as finance_crud
from app.crud.finance import annotate_payment_context
from app.crud.guest import get_guest_for_user
from app.db.session import get_db
from app.models.finance import Obligation, SimulatedPayment
from app.models.user_account import UserAccount
from app.schemas.finance import ObligationRead, SimulatedPaymentRead

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


@router.get("/obligations", response_model=list[ObligationRead])
def list_user_obligations(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """What the renter owes (pending/partially-paid/paid/etc.) across all of
    their occupancies -- rent and deposit due dates and amounts, previously
    only visible to the admin Finance page."""
    guest = get_guest_for_user(db, user)
    if not guest:
        return []
    return [finance_crud.to_obligation_read(o) for o in finance_crud.list_obligations_for_guest(db, guest)]


@router.post("/obligations/{obligation_id}/pay", response_model=SimulatedPaymentRead)
def pay_user_obligation(
    obligation_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Renter self-service payment of one of their own obligations -- simulated,
    same as every other payment in this system; confirms immediately rather
    than needing an admin to record it on the renter's behalf."""
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No guest record for this account")

    obligation = db.get(Obligation, obligation_id)
    if not obligation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Obligation not found")

    payment = finance_crud.pay_obligation_as_renter(db, guest, obligation)
    return SimulatedPaymentRead.model_validate(annotate_payment_context(payment))
