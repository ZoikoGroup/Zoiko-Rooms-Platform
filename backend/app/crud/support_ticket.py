"""Help & Support tickets (models/support_ticket.py).

A user files a ticket about one of their own records (or none), then trades
messages with support. Support -- any approved admin -- moves the status along:
IN_REVIEW, ACTION_REQUIRED (with what the user must send), RESOLVED or CLOSED
(with the outcome). A user reply to an ACTION_REQUIRED ticket puts it back
IN_REVIEW; a RESOLVED ticket can be reopened by the user, a CLOSED one cannot.
Every change notifies the other side in-app; notification ids carry the
message/status so repeat replies aren't dropped by the notification dedupe."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core import file_store
from app.core.config import settings
from app.crud import notification as notif_crud
from app.crud.guest import get_guest_for_user
from app.models.leasing import Application
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.property import Property
from app.models.room import Room
from app.models.support_ticket import (
    SUPPORT_LINKED_TYPES,
    SUPPORT_TICKET_CATEGORIES,
    SUPPORT_TICKET_STATUSES,
    SupportTicket,
    SupportTicketAttachment,
    SupportTicketMessage,
)
from app.models.user_account import UserAccount

ATTACHMENT_CATEGORY = "support_attachment"
MAX_FILES_PER_UPLOAD = 5
_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"%PDF-", ".pdf", "application/pdf"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(db: Session, *where):
    return db.scalar(
        select(SupportTicket)
        .options(
            selectinload(SupportTicket.messages).selectinload(SupportTicketMessage.attachments),
            selectinload(SupportTicket.attachments),
            selectinload(SupportTicket.user),
        )
        .where(*where)
    )


def get_user_ticket_or_404(db: Session, ref: str, user: UserAccount) -> SupportTicket:
    ticket = _load(db, SupportTicket.ref == ref, SupportTicket.user_id == user.id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Support ticket not found")
    return ticket


def get_ticket_or_404(db: Session, ticket_id: int) -> SupportTicket:
    ticket = _load(db, SupportTicket.id == ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Support ticket not found")
    return ticket


def list_user_tickets(db: Session, user: UserAccount) -> list[SupportTicket]:
    return list(db.scalars(
        select(SupportTicket)
        .options(
            selectinload(SupportTicket.messages).selectinload(SupportTicketMessage.attachments),
            selectinload(SupportTicket.attachments),
        )
        .where(SupportTicket.user_id == user.id)
        .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
    ))


def list_tickets(db: Session, status_filter: str | None = None) -> list[SupportTicket]:
    stmt = (
        select(SupportTicket)
        .options(selectinload(SupportTicket.user), selectinload(SupportTicket.messages), selectinload(SupportTicket.attachments))
        .order_by(SupportTicket.time_sensitive.desc(), SupportTicket.updated_at.desc())
    )
    if status_filter:
        stmt = stmt.where(SupportTicket.status == status_filter)
    return list(db.scalars(stmt))


# --- Linked items ----------------------------------------------------------


def _assert_owns_linked_item(db: Session, user: UserAccount, linked_type: str, linked_id: int) -> None:
    """A ticket can only point at a record the user is party to -- as the
    renter, or as the host owning the listing/property."""
    guest = get_guest_for_user(db, user)
    guest_id = guest.id if guest else None
    party_id = user.party_id
    ok = False
    if linked_type == "occupancy":
        occ = db.get(Occupancy, linked_id)
        if occ is not None:
            owner = db.scalar(select(Property.owner_party_id).join(Room, Room.property_id == Property.id).where(Room.id == occ.room_id))
            ok = (guest_id is not None and occ.guest_id == guest_id) or (party_id is not None and owner == party_id)
    elif linked_type == "application":
        app = db.get(Application, linked_id)
        if app is not None:
            listing_party = db.scalar(select(Listing.party_id).where(Listing.id == app.listing_id))
            ok = (guest_id is not None and app.guest_id == guest_id) or (party_id is not None and listing_party == party_id)
    elif linked_type == "property":
        prop = db.get(Property, linked_id)
        ok = prop is not None and party_id is not None and prop.owner_party_id == party_id
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Linked record not found")


# --- Attachments -----------------------------------------------------------


async def _read_attachments(files: list[UploadFile]) -> list[tuple[str, bytes, str, str]]:
    """Reads and validates every upload up front -- before any row is written
    -- returning (original_name, bytes, extension, content_type) each."""
    files = [f for f in files if f is not None and (f.filename or "")]
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Attach at most {MAX_FILES_PER_UPLOAD} files at a time")
    max_bytes = settings.evidence_document_max_size_mb * 1024 * 1024
    out = []
    for upload in files:
        contents = await upload.read()
        name = Path(upload.filename or "attachment").name[:255]
        if not contents:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{name} is empty")
        if len(contents) > max_bytes:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{name} exceeds the {settings.evidence_document_max_size_mb}MB limit",
            )
        sniffed = next(((ext, ct) for magic, ext, ct in _SIGNATURES if contents.startswith(magic)), None)
        if sniffed is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{name}: upload a PDF, JPG or PNG")
        out.append((name, contents, *sniffed))
    return out


def _store_attachments(
    db: Session, ticket: SupportTicket, message: SupportTicketMessage, files: list[tuple[str, bytes, str, str]],
) -> None:
    for name, contents, extension, content_type in files:
        stored = file_store.put(db, ATTACHMENT_CATEGORY, contents, extension=extension, content_type=content_type)
        db.add(SupportTicketAttachment(
            ticket_id=ticket.id, message_id=message.id, stored_filename=stored,
            original_filename=name, content_type=content_type, size=len(contents),
        ))
    db.flush()


def attachment_response(db: Session, ticket: SupportTicket, attachment_id: int):
    from urllib.parse import quote

    from fastapi.responses import Response

    attachment = next((a for a in ticket.attachments if a.id == attachment_id), None)
    if attachment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    data = file_store.read_or_404(db, ATTACHMENT_CATEGORY, attachment.stored_filename)
    return Response(
        content=data, media_type=attachment.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.original_filename)}",
            "Cache-Control": "no-store",
        },
    )


# --- User actions ----------------------------------------------------------


def _notify_support(db: Session, ticket: SupportTicket, *, title: str, message: str, event: str) -> None:
    kwargs = dict(
        title=title, message=message, notification_type=f"support_ticket.{event}",
        related_entity_type="support_ticket", related_entity_id=f"{ticket.id}:{uuid.uuid4().hex[:8]}",
    )
    if ticket.assigned_admin_id:
        notif_crud.notify_admin(db, ticket.assigned_admin_id, **kwargs)
    else:
        notif_crud.notify_all_admins(db, **kwargs)


def _notify_user(db: Session, ticket: SupportTicket, *, title: str, message: str, event: str) -> None:
    notif_crud.notify_user(
        db, ticket.user_id, title=title, message=message, notification_type=f"support_ticket.{event}",
        related_entity_type="support_ticket", related_entity_id=f"{ticket.ref}:{uuid.uuid4().hex[:8]}",
    )


async def create_ticket(
    db: Session, user: UserAccount, *, category: str, issue: str, description: str,
    linked_type: str | None, linked_id: int | None, linked_label: str,
    time_sensitive: bool, reply_channel: str, files: list[UploadFile],
) -> SupportTicket:
    category, issue, description = category.strip(), issue.strip(), description.strip()
    if category not in SUPPORT_TICKET_CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown support category")
    if not issue or len(issue) > 120:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose what the issue is about")
    if not description:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Describe what happened")
    if len(description) > 5000:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Description must be 5000 characters or fewer")
    if reply_channel not in ("both", "app"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown reply channel")
    if (linked_type is None) != (linked_id is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "linkedType and linkedId go together")
    if linked_type is not None:
        if linked_type not in SUPPORT_LINKED_TYPES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown linked record type")
        _assert_owns_linked_item(db, user, linked_type, linked_id)
    uploads = await _read_attachments(files)

    ticket = SupportTicket(
        ref=f"tmp-{uuid.uuid4().hex[:12]}", user_id=user.id, category=category, issue=issue,
        linked_type=linked_type, linked_id=linked_id, linked_label=linked_label.strip()[:255],
        description=description, time_sensitive=time_sensitive, reply_channel=reply_channel, status="SUBMITTED",
    )
    db.add(ticket)
    db.flush()
    ticket.ref = f"ZR-SUP-{10000 + ticket.id}"
    message = SupportTicketMessage(ticket_id=ticket.id, sender="USER", body=description)
    db.add(message)
    db.flush()
    _store_attachments(db, ticket, message, uploads)

    _notify_support(
        db, ticket, event="created",
        title=f"{'Time-sensitive' if time_sensitive else 'New'} support ticket {ticket.ref}",
        message=f"{issue} -- {user.full_name or user.email}",
    )
    _notify_user(
        db, ticket, event="received", title=f"We received {ticket.ref}",
        message=f"Your request about \"{issue}\" is with our support team.",
    )
    db.commit()
    return get_user_ticket_or_404(db, ticket.ref, user)


async def add_user_reply(
    db: Session, ticket: SupportTicket, user: UserAccount, body: str, files: list[UploadFile],
) -> SupportTicket:
    body = body.strip()
    if ticket.status == "CLOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This case is closed. Open a new request instead.")
    if ticket.status == "RESOLVED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This case is resolved. Reopen it to continue.")
    if len(body) > 5000:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Reply must be 5000 characters or fewer")
    uploads = await _read_attachments(files)
    if not body and not uploads:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Write a reply or attach a file")

    message = SupportTicketMessage(ticket_id=ticket.id, sender="USER", body=body or "Attached the requested file.")
    db.add(message)
    db.flush()
    _store_attachments(db, ticket, message, uploads)
    if ticket.status == "ACTION_REQUIRED":
        ticket.status = "IN_REVIEW"
        ticket.action_request = None
    ticket.updated_at = _now()
    _notify_support(db, ticket, event="user_replied", title=f"Reply on {ticket.ref}", message=message.body[:200])
    db.commit()
    return get_user_ticket_or_404(db, ticket.ref, user)


def reopen_ticket(db: Session, ticket: SupportTicket, user: UserAccount, reason: str) -> SupportTicket:
    if ticket.status != "RESOLVED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a resolved case can be reopened")
    reason = reason.strip() or "I'd like this case reviewed again."
    db.add(SupportTicketMessage(ticket_id=ticket.id, sender="USER", body=f"Reopened: {reason[:2000]}"))
    ticket.status = "IN_REVIEW"
    ticket.resolved_at = None
    ticket.updated_at = _now()
    _notify_support(db, ticket, event="reopened", title=f"{ticket.ref} reopened", message=reason[:200])
    db.commit()
    return get_user_ticket_or_404(db, ticket.ref, user)


def mark_solved_by_user(db: Session, ticket: SupportTicket, user: UserAccount) -> SupportTicket:
    """The user closes the loop themselves -- no one at support has to."""
    if ticket.status in ("RESOLVED", "CLOSED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This case is already resolved")
    ticket.status = "RESOLVED"
    ticket.resolution = ticket.resolution or "Marked as solved by you."
    ticket.resolved_at = _now()
    ticket.updated_at = _now()
    db.add(SupportTicketMessage(ticket_id=ticket.id, sender="USER", body="Marked as solved."))
    _notify_support(db, ticket, event="solved_by_user", title=f"{ticket.ref} solved", message="The user marked it as solved.")
    db.commit()
    return get_user_ticket_or_404(db, ticket.ref, user)


# --- Support (admin) actions ----------------------------------------------


def add_support_reply(db: Session, ticket: SupportTicket, admin_id: int, body: str) -> SupportTicket:
    if ticket.status == "CLOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This case is closed")
    db.add(SupportTicketMessage(ticket_id=ticket.id, sender="SUPPORT", author_admin_id=admin_id, body=body.strip()))
    if ticket.status == "SUBMITTED":
        ticket.status = "IN_REVIEW"
    if ticket.assigned_admin_id is None:
        ticket.assigned_admin_id = admin_id
    ticket.updated_at = _now()
    _notify_user(db, ticket, event="support_replied", title=f"Support replied on {ticket.ref}", message=body.strip()[:200])
    db.commit()
    return get_ticket_or_404(db, ticket.id)


def set_status(
    db: Session, ticket: SupportTicket, admin_id: int, new_status: str,
    action_request: str | None, resolution: str | None,
) -> SupportTicket:
    if new_status not in SUPPORT_TICKET_STATUSES or new_status == "SUBMITTED":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown status")
    if ticket.status == "CLOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This case is closed")
    if new_status == "ACTION_REQUIRED" and not (action_request or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say what the user needs to send")
    if new_status in ("RESOLVED", "CLOSED") and not (resolution or ticket.resolution or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give the outcome for the user")

    ticket.status = new_status
    if new_status == "ACTION_REQUIRED":
        ticket.action_request = action_request.strip()
    if new_status in ("RESOLVED", "CLOSED"):
        if resolution and resolution.strip():
            ticket.resolution = resolution.strip()
        ticket.resolved_at = ticket.resolved_at or _now()
        ticket.action_request = None
    if ticket.assigned_admin_id is None:
        ticket.assigned_admin_id = admin_id
    ticket.updated_at = _now()

    messages = {
        "IN_REVIEW": (f"{ticket.ref} is in review", "Our team is looking into your request."),
        "ACTION_REQUIRED": (f"Action needed on {ticket.ref}", ticket.action_request or ""),
        "RESOLVED": (f"{ticket.ref} resolved", ticket.resolution or ""),
        "CLOSED": (f"{ticket.ref} closed", ticket.resolution or ""),
    }
    title, body = messages[new_status]
    _notify_user(db, ticket, event=f"status_{new_status.lower()}", title=title, message=body[:200])
    db.commit()
    return get_ticket_or_404(db, ticket.id)
