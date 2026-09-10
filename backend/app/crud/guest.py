from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.crud.ids import dicebear_avatar, new_id
from app.models.booking import Booking
from app.models.guest import Guest
from app.models.user_account import UserAccount
from app.schemas.guest import GuestRead


def get_guest_for_user(db: Session, user: UserAccount) -> Guest | None:
    """The single source of truth for "which Guest record is this user's
    rental identity" -- looks up by the real user_account_id FK first. Falls
    back to an email match (and opportunistically backfills the FK when it
    finds one) only for a Guest row that predates the link or was created
    without it, so nothing already-matched silently becomes unreachable."""
    guest = db.scalar(select(Guest).where(Guest.user_account_id == user.id))
    if guest:
        return guest

    guest = db.scalar(select(Guest).where(Guest.email == user.email, Guest.user_account_id.is_(None)))
    if guest:
        guest.user_account_id = user.id
        db.flush()
    return guest


def get_user_for_guest(db: Session, guest: Guest) -> UserAccount | None:
    """The inverse of get_guest_for_user -- resolves the UserAccount (if any)
    behind a Guest, by the real FK first and an email match as a fallback for a
    guest that predates the link. Used wherever a caller needs the renter's own
    email/name (e.g. to send a transactional email) rather than just their
    notification recipient id."""
    if guest.user_account_id:
        return db.get(UserAccount, guest.user_account_id)
    return db.scalar(select(UserAccount).where(UserAccount.email == guest.email))


def get_or_create_guest_for_user(db: Session, user: UserAccount) -> Guest:
    """Get the Guest linked to this user, creating one (with the FK set from
    the start) if this is their first rental-lifecycle action."""
    guest = get_guest_for_user(db, user)
    if guest:
        return guest

    guest = Guest(
        id=new_id("G"),
        name=user.full_name,
        email=user.email,
        phone=user.phone,
        avatar=dicebear_avatar(user.full_name),
        location="",
        joined_at=date.today(),
        status="active",
        user_account_id=user.id,
    )
    db.add(guest)
    db.flush()
    return guest


def list_guests(db: Session) -> list[GuestRead]:
    guests = db.scalars(select(Guest).options(joinedload(Guest.bookings).joinedload(Booking.listing)).order_by(Guest.name)).unique()
    return [to_guest_read(g) for g in guests]


def to_guest_read(guest: Guest) -> GuestRead:
    total_bookings = len(guest.bookings)
    total_spent = sum(b.total_amount for b in guest.bookings if b.payment_status == "paid")
    return GuestRead(
        id=guest.id,
        name=guest.name,
        email=guest.email,
        phone=guest.phone,
        avatar=guest.avatar,
        location=guest.location,
        total_bookings=total_bookings,
        total_spent=total_spent,
        joined_at=guest.joined_at,
        status=guest.status,
    )
