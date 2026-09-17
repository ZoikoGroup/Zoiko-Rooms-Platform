import functools
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.notification import Notification
from app.models.user_account import UserAccount

logger = logging.getLogger("uvicorn.error")


def _never_raises(fn):
    """Notifying is always best-effort with respect to the business change that
    triggered it -- every public notify_* entry point is wrapped so that a
    failure anywhere in it (the recipient lookup query, not just the insert in
    _create) is logged and swallowed rather than propagated into the caller's
    transaction. Applied to the leaf functions (notify_user/_by_party/_by_guest,
    notify_admin); notify_all_admins/notify_all_super_admins fan out through
    notify_admin and are therefore already covered."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            logger.exception(
                "notification: %s failed -- the triggering business transaction is unaffected", fn.__name__
            )
            return None

    return wrapper


def _create(
    db: Session,
    *,
    recipient_type: str,
    recipient_user_id: int | None,
    recipient_admin_id: int | None,
    title: str,
    message: str,
    notification_type: str,
    related_entity_type: str,
    related_entity_id: str,
) -> Notification | None:
    """Creates one notification row, silently no-op on an exact duplicate (same
    type/entity/recipient -- see the model's unique constraint) instead of
    raising. Runs in a SAVEPOINT so neither a duplicate nor any other failure
    here can abort the caller's outer transaction -- notifying is always
    best-effort with respect to the business change that triggered it, which is
    why this is always called well before the caller's own db.commit()."""
    recipient_key = f"user:{recipient_user_id}" if recipient_type == "user" else f"admin:{recipient_admin_id}"
    notification = Notification(
        recipient_type=recipient_type,
        recipient_user_id=recipient_user_id,
        recipient_admin_id=recipient_admin_id,
        recipient_key=recipient_key,
        title=title,
        message=message,
        notification_type=notification_type,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
    )
    try:
        with db.begin_nested():
            db.add(notification)
            db.flush()
    except IntegrityError:
        logger.info(
            "notification: duplicate suppressed (type=%s entity=%s:%s recipient=%s)",
            notification_type, related_entity_type, related_entity_id, recipient_key,
        )
        return None
    except Exception:
        logger.exception(
            "notification: failed to create (type=%s entity=%s:%s recipient=%s) -- "
            "the triggering business transaction is unaffected",
            notification_type, related_entity_type, related_entity_id, recipient_key,
        )
        return None
    return notification


@_never_raises
def notify_user(
    db: Session, user_id: int, *, title: str, message: str, notification_type: str,
    related_entity_type: str = "", related_entity_id: str = "",
) -> Notification | None:
    return _create(
        db, recipient_type="user", recipient_user_id=user_id, recipient_admin_id=None,
        title=title, message=message, notification_type=notification_type,
        related_entity_type=related_entity_type, related_entity_id=related_entity_id,
    )


@_never_raises
def notify_user_by_party(db: Session, party_id: int | None, **kwargs) -> Notification | None:
    """Looks up the active UserAccount for a party -- the same party_id ->
    UserAccount lookup already used across hosting/sublet crud -- and notifies
    them. No-ops silently if the party has no user account (e.g. it's an
    admin-owned party, which has nobody on the USER side to notify)."""
    if not party_id:
        return None
    user = db.scalar(
        select(UserAccount).where(UserAccount.party_id == party_id, UserAccount.is_active.is_(True))
    )
    if not user:
        return None
    return notify_user(db, user.id, **kwargs)


@_never_raises
def notify_user_by_guest(db: Session, guest: Guest, **kwargs) -> Notification | None:
    """Notifies the UserAccount linked to a Guest via the real
    user_account_id FK (see models/guest.py). Falls back to an email match
    (and opportunistically backfills the FK) for a guest that hasn't gone
    through crud.guest.get_guest_for_user yet -- same self-healing rule used
    there, applied from the guest side. No-ops silently if neither resolves
    (e.g. a walk-in tenant from the legacy admin Booking flow with no
    self-service account at all), same as notify_user_by_party."""
    user_id = guest.user_account_id
    if not user_id:
        user = db.scalar(select(UserAccount).where(UserAccount.email == guest.email))
        if not user:
            return None
        guest.user_account_id = user.id
        db.flush()
        user_id = user.id
    return notify_user(db, user_id, **kwargs)


@_never_raises
def notify_admin(
    db: Session, admin_id: int, *, title: str, message: str, notification_type: str,
    related_entity_type: str = "", related_entity_id: str = "",
) -> Notification | None:
    return _create(
        db, recipient_type="admin", recipient_user_id=None, recipient_admin_id=admin_id,
        title=title, message=message, notification_type=notification_type,
        related_entity_type=related_entity_type, related_entity_id=related_entity_id,
    )


def notify_all_super_admins(db: Session, **kwargs) -> list[Notification]:
    """Fans out into one row per active super admin so each has an independent
    read state -- see the model docstring for why this isn't a single broadcast row."""
    admin_ids = db.scalars(
        select(AdminUser.id).where(AdminUser.role == "super_admin", AdminUser.is_active.is_(True))
    )
    created = []
    for admin_id in admin_ids:
        row = notify_admin(db, admin_id, **kwargs)
        if row:
            created.append(row)
    return created


def notify_all_admins(db: Session, **kwargs) -> list[Notification]:
    """Same fan-out as notify_all_super_admins, but every active admin regardless
    of role -- for operational events (e.g. a listing submitted for review) that
    any admin, not just super admins, should see and can act on."""
    admin_ids = db.scalars(
        select(AdminUser.id).where(AdminUser.is_active.is_(True), AdminUser.approval_status == "approved")
    )
    created = []
    for admin_id in admin_ids:
        row = notify_admin(db, admin_id, **kwargs)
        if row:
            created.append(row)
    return created


def list_for_user(db: Session, user_id: int, limit: int = 50) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.recipient_user_id == user_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
    )


def list_for_admin(db: Session, admin_id: int, limit: int = 50) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.recipient_admin_id == admin_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
    )


def count_unread_for_user(db: Session, user_id: int) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient_user_id == user_id, Notification.is_read.is_(False))
        )
        or 0
    )


def count_unread_for_admin(db: Session, admin_id: int) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient_admin_id == admin_id, Notification.is_read.is_(False))
        )
        or 0
    )


def mark_read_for_user(db: Session, notification_id: int, user_id: int) -> Notification | None:
    """Scoped to the caller's own user_id -- a USER can never mark (or even
    address) another USER's notification, regardless of what id is requested."""
    notification = db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.recipient_user_id == user_id)
    )
    if not notification:
        return None
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)
    return notification


def mark_read_for_admin(db: Session, notification_id: int, admin_id: int) -> Notification | None:
    """Scoped to the caller's own admin_id -- one admin can never mark another
    admin's notification, super admin included."""
    notification = db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.recipient_admin_id == admin_id)
    )
    if not notification:
        return None
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)
    return notification


def mark_all_read_for_user(db: Session, user_id: int) -> int:
    now = datetime.now(timezone.utc)
    result = db.execute(
        select(Notification).where(Notification.recipient_user_id == user_id, Notification.is_read.is_(False))
    )
    rows = list(result.scalars())
    for row in rows:
        row.is_read = True
        row.read_at = now
    db.commit()
    return len(rows)


def mark_all_read_for_admin(db: Session, admin_id: int) -> int:
    now = datetime.now(timezone.utc)
    result = db.execute(
        select(Notification).where(Notification.recipient_admin_id == admin_id, Notification.is_read.is_(False))
    )
    rows = list(result.scalars())
    for row in rows:
        row.is_read = True
        row.read_at = now
    db.commit()
    return len(rows)
