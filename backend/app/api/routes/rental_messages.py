"""Host <-> renter messaging (one thread per application per renter) -- see
crud/rental_message.py. The renter and host routes mirror each other; each side
only ever reaches its own threads. A host picks which renter's thread with
?guestId= (defaults to the current occupant, else the applicant)."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.crud import rental_message as crud
from app.models.user_account import UserAccount
from app.schemas.rental_message import RentalConversationRead, RentalMessageCreate, RentalMessageRead, RenterThreadRead

renter_router = APIRouter(prefix="/api/users/rentals", tags=["rental-messages-renter"], dependencies=[Depends(get_current_user)])
host_router = APIRouter(prefix="/api/users/hosting", tags=["rental-messages-host"], dependencies=[Depends(get_current_user)])


@renter_router.get("/conversations", response_model=list[RentalConversationRead])
def list_renter_conversations(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.list_conversations_for_renter(db, user)


@renter_router.get("/applications/{application_id}/messages", response_model=list[RentalMessageRead])
def list_renter_messages(application_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    application, guest = crud.get_renter_thread_or_403(db, application_id, user)
    return crud.list_messages(db, application, guest, crud.RENTER)


@renter_router.post(
    "/applications/{application_id}/messages", response_model=RentalMessageRead, status_code=status.HTTP_201_CREATED,
)
def send_renter_message(
    application_id: int, payload: RentalMessageCreate,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    application, guest = crud.get_renter_thread_or_403(db, application_id, user)
    return crud.send_message(db, application, guest, user, crud.RENTER, payload.body)


@host_router.get("/conversations", response_model=list[RentalConversationRead])
def list_host_conversations(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.list_conversations_for_host(db, user)


@host_router.get("/applications/{application_id}/renters", response_model=list[RenterThreadRead])
def list_host_renter_threads(application_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Who the host can message on this application: the original renter and
    any subtenant, each with their own thread (pass their guestId)."""
    from app.crud.leasing import get_application_for_host_or_404

    application = get_application_for_host_or_404(db, application_id, user)
    return crud.list_renter_threads_for_host(db, application)


@host_router.get("/applications/{application_id}/messages", response_model=list[RentalMessageRead])
def list_host_messages(
    application_id: int, guest_id: str | None = Query(default=None, alias="guestId"),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    application, guest = crud.get_host_thread_or_403(db, application_id, user, guest_id)
    return crud.list_messages(db, application, guest, crud.HOST)


@host_router.post(
    "/applications/{application_id}/messages", response_model=RentalMessageRead, status_code=status.HTTP_201_CREATED,
)
def send_host_message(
    application_id: int, payload: RentalMessageCreate, guest_id: str | None = Query(default=None, alias="guestId"),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    application, guest = crud.get_host_thread_or_403(db, application_id, user, guest_id)
    return crud.send_message(db, application, guest, user, crud.HOST, payload.body)


@renter_router.get("/sublet-requests/{sublet_request_id}/messages", response_model=list[RentalMessageRead])
def list_sublet_messages(sublet_request_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """Renter <-> subtenant thread on a sublet request -- either side."""
    sublet_request, role = crud.get_sublet_thread_or_403(db, sublet_request_id, user)
    return crud.list_sublet_messages(db, sublet_request, role)


@renter_router.post(
    "/sublet-requests/{sublet_request_id}/messages", response_model=RentalMessageRead, status_code=status.HTTP_201_CREATED,
)
def send_sublet_message(
    sublet_request_id: int, payload: RentalMessageCreate,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    sublet_request, role = crud.get_sublet_thread_or_403(db, sublet_request_id, user)
    return crud.send_sublet_message(db, sublet_request, user, role, payload.body)
