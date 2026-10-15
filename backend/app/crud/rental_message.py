"""Host <-> renter messaging, one conversation per application per renter.

The renter side of a thread is either the applicant (application.guest_id) or
the tenant now occupying the room under that application -- after a full
sublet handover that's the incoming tenant, who gets their own thread with the
host rather than the previous renter's. The host side is whoever owns the
listing (crud/leasing.py:get_application_for_host_or_404). Each new message
sends the other side an in-app notification -- no email. The notification's
entity id is "<application>:<message>" so each message notifies; keyed on the
application alone, the notification dedupe would drop every message after
the first."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.guest import get_guest_for_user
from app.crud.leasing import get_application_for_host_or_404, get_application_or_404
from app.crud.party import party_id_for_listing
from app.crud.user import get_user_by_party_id
from app.models.guest import Guest
from app.models.leasing import Application, Offer
from app.models.occupancy import Occupancy
from app.models.rental_message import RentalConversation, RentalMessage
from app.models.user_account import UserAccount
from app.schemas.rental_message import RentalConversationRead, RentalMessageRead

RENTER, HOST = "RENTER", "HOST"


def _renter_guest_ids(db: Session, application: Application) -> set[str]:
    """Who may be the renter side of a thread on this application: the
    applicant, plus anyone occupying the room under it (a subtenant after a
    full handover)."""
    ids = {application.guest_id}
    ids.update(db.scalars(
        select(Occupancy.guest_id).join(Offer, Offer.id == Occupancy.offer_id).where(Offer.application_id == application.id)
    ))
    return ids


def get_renter_thread_or_403(db: Session, application_id: int, user: UserAccount) -> tuple[Application, Guest]:
    application = get_application_or_404(db, application_id)
    guest = get_guest_for_user(db, user)
    if not guest or guest.id not in _renter_guest_ids(db, application):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only message about your own rentals")
    return application, guest


def get_host_thread_or_403(
    db: Session, application_id: int, user: UserAccount, guest_id: str | None,
) -> tuple[Application, Guest]:
    """The host picks which renter's thread to open; defaults to the current
    occupant if there is one, else the applicant."""
    application = get_application_for_host_or_404(db, application_id, user)
    allowed = _renter_guest_ids(db, application)
    if guest_id is None:
        occupant = db.scalar(
            select(Occupancy.guest_id).join(Offer, Offer.id == Occupancy.offer_id)
            .where(Offer.application_id == application.id).order_by(Occupancy.id.desc())
        )
        guest_id = occupant or application.guest_id
    has_thread = db.scalar(select(RentalConversation.id).where(
        RentalConversation.application_id == application.id, RentalConversation.guest_id == guest_id,
    ))
    if guest_id not in allowed and not has_thread:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such renter on this application")
    guest = db.get(Guest, guest_id)
    if guest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Renter not found")
    return application, guest


def _conversation_for(db: Session, application: Application, guest: Guest) -> RentalConversation | None:
    return db.scalar(select(RentalConversation).where(
        RentalConversation.application_id == application.id, RentalConversation.guest_id == guest.id,
    ))


def _host_name(db: Session, application: Application) -> str:
    listing = application.listing
    host_user = get_user_by_party_id(db, party_id_for_listing(listing)) if listing else None
    if host_user and host_user.full_name:
        return host_user.full_name
    return (listing.contact_name if listing else "") or "Host"


def _to_message_read(message: RentalMessage, viewer_role: str) -> RentalMessageRead:
    return RentalMessageRead(
        id=message.id, sender_role=message.sender_role, body=message.body,
        created_at=message.created_at, read_at=message.read_at, mine=message.sender_role == viewer_role,
    )


def list_messages(db: Session, application: Application, guest: Guest, viewer_role: str) -> list[RentalMessageRead]:
    """Returns the thread and marks the other side's messages as read."""
    conversation = _conversation_for(db, application, guest)
    if conversation is None:
        return []
    now = datetime.now(timezone.utc)
    changed = False
    for message in conversation.messages:
        if message.sender_role != viewer_role and message.read_at is None:
            message.read_at = now
            changed = True
    if changed:
        db.commit()
    return [_to_message_read(m, viewer_role) for m in conversation.messages]


def send_message(
    db: Session, application: Application, guest: Guest, sender: UserAccount, sender_role: str, body: str,
) -> RentalMessageRead:
    text = body.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message can't be empty")
    conversation = _conversation_for(db, application, guest)
    if conversation is None:
        conversation = RentalConversation(application_id=application.id, guest_id=guest.id)
        db.add(conversation)
        db.flush()

    message = RentalMessage(conversation_id=conversation.id, sender_role=sender_role, sender_user_id=sender.id, body=text)
    db.add(message)
    conversation.last_message_at = datetime.now(timezone.utc)
    db.flush()

    listing_name = application.listing.name if application.listing else "your rental"
    preview = text if len(text) <= 120 else text[:117] + "..."
    if sender_role == RENTER:
        notif_crud.notify_user_by_party(
            db, party_id_for_listing(application.listing),
            title=f"New message from {guest.name or 'a renter'}",
            message=f'About "{listing_name}": {preview}',
            notification_type="rental_message.received_for_host",
            related_entity_type="application", related_entity_id=f"{application.id}:{message.id}",
        )
    else:
        notif_crud.notify_user_by_guest(
            db, guest,
            title=f"New message from {_host_name(db, application)}",
            message=f'About "{listing_name}": {preview}',
            notification_type="rental_message.received",
            related_entity_type="application", related_entity_id=f"{application.id}:{message.id}",
        )
    db.commit()
    db.refresh(message)
    return _to_message_read(message, sender_role)


def _unread_counts(db: Session, conversation_ids: list[int], viewer_role: str) -> dict[int, int]:
    if not conversation_ids:
        return {}
    rows = db.execute(
        select(RentalMessage.conversation_id, func.count())
        .where(
            RentalMessage.conversation_id.in_(conversation_ids),
            RentalMessage.sender_role != viewer_role,
            RentalMessage.read_at.is_(None),
        )
        .group_by(RentalMessage.conversation_id)
    ).all()
    return {cid: n for cid, n in rows}


def _to_conversation_reads(db: Session, conversations: list[RentalConversation], viewer_role: str) -> list[RentalConversationRead]:
    conversations = sorted(conversations, key=lambda c: c.last_message_at or c.created_at, reverse=True)
    unread = _unread_counts(db, [c.id for c in conversations], viewer_role)
    out = []
    for c in conversations:
        application = c.application
        last = c.messages[-1] if c.messages else None
        out.append(RentalConversationRead(
            application_id=application.id,
            guest_id=c.guest_id,
            listing_id=application.listing_id,
            listing_name=application.listing.name if application.listing else "",
            counterpart_name=_host_name(db, application) if viewer_role == RENTER else (c.guest.name if c.guest else "Renter"),
            last_message_at=c.last_message_at,
            last_message_preview=(last.body[:120] if last else ""),
            unread_count=unread.get(c.id, 0),
        ))
    return out


def list_conversations_for_renter(db: Session, user: UserAccount) -> list[RentalConversationRead]:
    guest = get_guest_for_user(db, user)
    if guest is None:
        return []
    conversations = list(db.scalars(select(RentalConversation).where(RentalConversation.guest_id == guest.id)))
    return _to_conversation_reads(db, conversations, RENTER)


def list_conversations_for_host(db: Session, user: UserAccount) -> list[RentalConversationRead]:
    if not user.party_id:
        return []
    conversations = [
        c for c in db.scalars(select(RentalConversation))
        if c.application.listing and party_id_for_listing(c.application.listing) == user.party_id
    ]
    return _to_conversation_reads(db, conversations, HOST)
