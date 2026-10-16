"""Help & Support tickets -- see crud/support_ticket.py. Users reach only their
own tickets, keyed by ref (ZR-SUP-xxxxx). Support staff (any approved admin)
answer and move tickets along from /api/admin/support/tickets."""

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user, get_db
from app.crud import support_ticket as crud
from app.models.admin_user import AdminUser
from app.models.support_ticket import SupportTicket
from app.models.user_account import UserAccount
from app.schemas.support_ticket import (
    AdminSupportStatusUpdate,
    AdminSupportTicketRead,
    SupportReplyCreate,
    SupportTicketRead,
)

router = APIRouter(prefix="/api/users/support", tags=["support-user"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/api/admin/support", tags=["support-admin"], dependencies=[Depends(get_current_admin)])


class _ReopenRequest(BaseModel):
    reason: str = Field(default="", max_length=2000)


@router.get("/tickets", response_model=list[SupportTicketRead])
def list_my_tickets(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.list_user_tickets(db, user)


@router.post("/tickets", response_model=SupportTicketRead, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    request: Request,
    background: BackgroundTasks,
    category: str = Form(...),
    issue: str = Form(...),
    description: str = Form(...),
    linked_type: str | None = Form(default=None, alias="linkedType"),
    linked_id: int | None = Form(default=None, alias="linkedId"),
    linked_label: str = Form(default="", alias="linkedLabel"),
    time_sensitive: bool = Form(default=False, alias="timeSensitive"),
    reply_channel: str = Form(default="both", alias="replyChannel"),
    files: list[UploadFile] = File(default=[]),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ticket = await crud.create_ticket(
        db, user, category=category, issue=issue, description=description,
        linked_type=linked_type or None, linked_id=linked_id, linked_label=linked_label,
        time_sensitive=time_sensitive, reply_channel=reply_channel, files=files,
    )
    # Automatic first reply from the knowledge base, after the response is sent.
    from app.services.support_assistant import post_first_reply_in_background

    provider = request.app.dependency_overrides.get(get_db, get_db)
    background.add_task(post_first_reply_in_background, provider, ticket.id)
    return ticket


@router.get("/tickets/{ref}", response_model=SupportTicketRead)
def get_my_ticket(ref: str, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.get_user_ticket_or_404(db, ref, user)


@router.post("/tickets/{ref}/messages", response_model=SupportTicketRead)
async def reply_to_my_ticket(
    ref: str,
    body: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ticket = crud.get_user_ticket_or_404(db, ref, user)
    return await crud.add_user_reply(db, ticket, user, body, files)


@router.post("/tickets/{ref}/reopen", response_model=SupportTicketRead)
def reopen_my_ticket(
    ref: str, payload: _ReopenRequest, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    ticket = crud.get_user_ticket_or_404(db, ref, user)
    return crud.reopen_ticket(db, ticket, user, payload.reason)


@router.post("/tickets/{ref}/solved", response_model=SupportTicketRead)
def mark_my_ticket_solved(ref: str, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """The user says their problem is solved (e.g. by the automatic reply)."""
    ticket = crud.get_user_ticket_or_404(db, ref, user)
    return crud.mark_solved_by_user(db, ticket, user)


@router.get("/tickets/{ref}/attachments/{attachment_id}")
def download_my_attachment(
    ref: str, attachment_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    ticket = crud.get_user_ticket_or_404(db, ref, user)
    return crud.attachment_response(db, ticket, attachment_id)


# --- Support staff ---------------------------------------------------------


def _admin_read(ticket: SupportTicket) -> AdminSupportTicketRead:
    read = AdminSupportTicketRead.model_validate(ticket)
    if ticket.user is not None:
        read.user_email = ticket.user.email or ""
        read.user_name = ticket.user.full_name or ""
    return read


@admin_router.get("/tickets", response_model=list[AdminSupportTicketRead])
def list_all_tickets(status_filter: str | None = Query(default=None, alias="status"), db: Session = Depends(get_db)):
    return [_admin_read(t) for t in crud.list_tickets(db, status_filter)]


@admin_router.get("/tickets/{ticket_id}", response_model=AdminSupportTicketRead)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    return _admin_read(crud.get_ticket_or_404(db, ticket_id))


@admin_router.post("/tickets/{ticket_id}/messages", response_model=AdminSupportTicketRead)
def reply_to_ticket(
    ticket_id: int, payload: SupportReplyCreate,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    ticket = crud.get_ticket_or_404(db, ticket_id)
    return _admin_read(crud.add_support_reply(db, ticket, admin.id, payload.body))


@admin_router.post("/tickets/{ticket_id}/status", response_model=AdminSupportTicketRead)
def update_ticket_status(
    ticket_id: int, payload: AdminSupportStatusUpdate,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    ticket = crud.get_ticket_or_404(db, ticket_id)
    return _admin_read(crud.set_status(db, ticket, admin.id, payload.status, payload.action_request, payload.resolution))


@admin_router.get("/tickets/{ticket_id}/attachments/{attachment_id}")
def download_attachment(ticket_id: int, attachment_id: int, db: Session = Depends(get_db)):
    return crud.attachment_response(db, crud.get_ticket_or_404(db, ticket_id), attachment_id)
