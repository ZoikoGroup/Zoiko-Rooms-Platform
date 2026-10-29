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
from app.models.rental_message import RentalConversation, RentalMessage, SubletMessage
from app.models.sublet_request import CO_TENANCY_ARRANGEMENT_TYPES, REPLACING_ARRANGEMENT_TYPES, SubletRequest
from app.models.user_account import UserAccount
from app.schemas.rental_message import RentalConversationRead, RentalMessageRead

RENTER, HOST = "RENTER", "HOST"
# Sublet threads: RENTER is the renter who asked to sublet, SUBTENANT the
# person they named.
SUBTENANT = "SUBTENANT"


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


def list_renter_threads_for_host(db: Session, application: Application) -> list[dict]:
    """Every renter the host can message on this application: the original
    applicant and anyone occupying the room under it (a subtenant after a
    full handover), with unread counts -- so the host sees which person each
    thread is with instead of everything under the applicant's name."""
    occupant_ids = list(db.scalars(
        select(Occupancy.guest_id).join(Offer, Offer.id == Occupancy.offer_id)
        .where(Offer.application_id == application.id).order_by(Occupancy.id.desc())
    ))
    current_occupant = occupant_ids[0] if occupant_ids else None
    ordered = [application.guest_id] + [g for g in dict.fromkeys(occupant_ids) if g != application.guest_id]
    conversations = {
        c.guest_id: c for c in db.scalars(
            select(RentalConversation).where(RentalConversation.application_id == application.id)
        )
    }
    unread = _unread_counts(db, [c.id for c in conversations.values()], HOST)
    out = []
    for guest_id in ordered:
        guest = db.get(Guest, guest_id)
        conversation = conversations.get(guest_id)
        out.append({
            "guest_id": guest_id,
            "name": guest.name if guest else "Renter",
            "role": "APPLICANT" if guest_id == application.guest_id else "SUBTENANT",
            "is_current_occupant": guest_id == current_occupant,
            "unread_count": unread.get(conversation.id, 0) if conversation else 0,
            "last_message_at": conversation.last_message_at if conversation else None,
        })
    return out


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
        # A subtenant never applied, so their copy opens My Rentals rather
        # than the Applications page.
        notif_crud.notify_user_by_guest(
            db, guest,
            title=f"New message from {_host_name(db, application)}",
            message=f'About "{listing_name}": {preview}',
            notification_type=(
                "rental_message.received" if guest.id == application.guest_id else "rental_message.received_subtenant"
            ),
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


# -- renter <-> subtenant ------------------------------------------------------

THREAD_PEER, THREAD_HOST = "PEER", "HOST"
# The person added to a room (a co-tenant, a permitted occupant) or who takes it over (a replacement or full
# handover) talks to the host in this thread, kept with the sublet itself -- never in the renter's application thread.
HOST_THREAD_ARRANGEMENT_TYPES = ("ADD_CO_TENANT", "ADDITIONAL_OCCUPANT", "ASSIGNMENT_FULL", "REPLACEMENT_OCCUPANT")
# Someone taking the room over only has a rental -- and so a thread -- once the host has approved.
_HOST_THREAD_AFTER_APPROVAL_TYPES = ("ASSIGNMENT_FULL", "REPLACEMENT_OCCUPANT")
# Before the host has the request, the host has no thread with the person named in it.
_HOST_THREAD_NOT_YET_STATUSES = ("draft", "declined_by_co_tenant")


def host_thread_available(sublet_request: SubletRequest) -> bool:
    return (
        bool(sublet_request.proposed_renter_party_id)
        and sublet_request.arrangement_type in HOST_THREAD_ARRANGEMENT_TYPES
        and sublet_request.status not in _HOST_THREAD_NOT_YET_STATUSES
        and (sublet_request.arrangement_type not in _HOST_THREAD_AFTER_APPROVAL_TYPES or sublet_request.status == "approved")
    )


def get_sublet_thread_or_403(
    db: Session, sublet_request_id: int, user: UserAccount, thread: str = THREAD_PEER,
) -> tuple[SubletRequest, str]:
    """The renter thread: the two people on a sublet request can message each other once it's been
    submitted -- the renter who asked (requested_by_guest_id) and the person they named
    (proposed_renter_party_id). The host thread: the host of the listing and the person named, for a
    co-tenant or permitted occupant. Returns the caller's role."""
    sublet_request = db.get(SubletRequest, sublet_request_id)
    if sublet_request is None or sublet_request.status == "draft" or not sublet_request.proposed_renter_party_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")
    if thread == THREAD_HOST:
        if not host_thread_available(sublet_request):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Sublet request not found")
        listing = sublet_request.current_occupancy.listing if sublet_request.current_occupancy else None
        if user.party_id and sublet_request.proposed_renter_party_id == user.party_id:
            return sublet_request, SUBTENANT
        if user.party_id and listing is not None and party_id_for_listing(listing) == user.party_id:
            return sublet_request, HOST
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only message about your own sublets")
    guest = get_guest_for_user(db, user)
    if guest is not None and sublet_request.requested_by_guest_id == guest.id:
        return sublet_request, RENTER
    if user.party_id and sublet_request.proposed_renter_party_id == user.party_id:
        return sublet_request, SUBTENANT
    raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only message about your own sublets")


def _to_sublet_message_read(message: SubletMessage, viewer_role: str) -> RentalMessageRead:
    return RentalMessageRead(
        id=message.id, sender_role=message.sender_role, body=message.body,
        created_at=message.created_at, read_at=message.read_at, mine=message.sender_role == viewer_role,
    )


def list_sublet_messages(
    db: Session, sublet_request: SubletRequest, viewer_role: str, thread: str = THREAD_PEER,
) -> list[RentalMessageRead]:
    """Returns the thread and marks the other side's messages as read."""
    messages = list(db.scalars(
        select(SubletMessage)
        .where(SubletMessage.sublet_request_id == sublet_request.id, SubletMessage.thread == thread)
        .order_by(SubletMessage.id)
    ))
    now = datetime.now(timezone.utc)
    changed = False
    for message in messages:
        if message.sender_role != viewer_role and message.read_at is None:
            message.read_at = now
            changed = True
    if changed:
        db.commit()
    return [_to_sublet_message_read(m, viewer_role) for m in messages]


def send_sublet_message(
    db: Session, sublet_request: SubletRequest, sender: UserAccount, sender_role: str, body: str,
    thread: str = THREAD_PEER,
) -> RentalMessageRead:
    text = body.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message can't be empty")
    message = SubletMessage(
        sublet_request_id=sublet_request.id, sender_role=sender_role, sender_user_id=sender.id, body=text, thread=thread,
    )
    db.add(message)
    db.flush()

    listing = sublet_request.current_occupancy.listing if sublet_request.current_occupancy else None
    listing_name = listing.name if listing else "your rental"
    preview = text if len(text) <= 120 else text[:117] + "..."
    notification = dict(
        title=f"New message from {sender.full_name or 'your sublet contact'}",
        message=f'About the sublet of "{listing_name}": {preview}',
        related_entity_type="sublet_request", related_entity_id=f"{sublet_request.id}:{message.id}",
    )
    if thread == THREAD_HOST:
        # Host <-> the person added: each side tells the other.
        if sender_role == SUBTENANT:
            notif_crud.notify_user_by_party(
                db, party_id_for_listing(listing) if listing else None,
                notification_type="sublet_message.host_received", **notification,
            )
        else:
            notif_crud.notify_user_by_party(
                db, sublet_request.proposed_renter_party_id,
                notification_type="sublet_message.received_subtenant", **notification,
            )
    # The renter's copy opens their Sublets page; the subtenant's, My Rentals.
    elif sender_role == SUBTENANT:
        requester = db.get(Guest, sublet_request.requested_by_guest_id) if sublet_request.requested_by_guest_id else None
        if requester is not None:
            notif_crud.notify_user_by_guest(db, requester, notification_type="sublet_message.received", **notification)
    else:
        notif_crud.notify_user_by_party(
            db, sublet_request.proposed_renter_party_id, notification_type="sublet_message.received_subtenant", **notification,
        )
    db.commit()
    db.refresh(message)
    return _to_sublet_message_read(message, sender_role)


def sublet_held_through(db: Session, occupancy: Occupancy) -> SubletRequest | None:
    """The approved sublet request this occupancy's current occupant got the
    room through, if any: a full handover (the original occupancy now holds
    the incoming tenant) or a subtenancy/lodging (its own agreement, recorded
    as the request's new_agreement_id)."""
    handover = db.scalar(
        select(SubletRequest).where(
            SubletRequest.current_occupancy_id == occupancy.id,
            SubletRequest.status == "approved",
            SubletRequest.arrangement_type.in_(REPLACING_ARRANGEMENT_TYPES),
        ).limit(1)
    )
    if handover is not None:
        return handover if handover.requested_by_guest_id != occupancy.guest_id else None
    agreement = occupancy.offer.agreement if occupancy.offer else None
    if agreement is None:
        return None
    return db.scalar(
        select(SubletRequest).where(
            SubletRequest.new_agreement_id == agreement.id,
            SubletRequest.arrangement_type.in_(CO_TENANCY_ARRANGEMENT_TYPES),
        ).limit(1)
    )
