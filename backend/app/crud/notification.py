import functools
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.admin_user import AdminSettings
from app.models.notification import (
    Notification,
    admin_preference_for_notification_type,
    category_for_notification_type,
    priority_for_notification_type,
)
from app.models.notification_preference import NotificationPreference
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


def _is_suppressed_by_preference(db: Session, recipient_key: str, *, category: str) -> bool:
    """Section 11 gap: a recipient's hard category opt-out suppresses the
    notification outright. Quiet hours were removed (ZR-61): nobody could tell
    which notifications counted as urgent, so they only ever caused missed
    notices; any window saved earlier is ignored."""
    pref = db.scalar(select(NotificationPreference).where(NotificationPreference.recipient_key == recipient_key))
    return pref is not None and category in pref.opted_out_categories


def _admin_switched_off(db: Session, admin_id: int | None, notification_type: str) -> bool:
    """The admin turned this kind of notification off in Settings >
    Notification Preferences. An admin with no saved settings gets the column
    defaults (new bookings and payments on; reviews and marketing off)."""
    attribute = admin_preference_for_notification_type(notification_type)
    if attribute is None or admin_id is None:
        return False
    row = db.scalar(select(AdminSettings).where(AdminSettings.admin_user_id == admin_id))
    if row is None:
        return not AdminSettings.__table__.c[attribute].default.arg
    return not getattr(row, attribute)


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
    raising, or on a recipient's own preference suppressing it (see
    _is_suppressed_by_preference). Runs in a SAVEPOINT so neither a duplicate
    nor any other failure here can abort the caller's outer transaction --
    notifying is always best-effort with respect to the business change that
    triggered it, which is why this is always called well before the
    caller's own db.commit()."""
    recipient_key = f"user:{recipient_user_id}" if recipient_type == "user" else f"admin:{recipient_admin_id}"
    if recipient_type == "admin" and _admin_switched_off(db, recipient_admin_id, notification_type):
        logger.info("notification: switched off by admin preference (type=%s recipient=%s)", notification_type, recipient_key)
        return None
    category = category_for_notification_type(notification_type)
    priority = priority_for_notification_type(notification_type)
    if _is_suppressed_by_preference(db, recipient_key, category=category):
        logger.info("notification: suppressed by recipient preference (type=%s recipient=%s)", notification_type, recipient_key)
        return None
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
        category=category,
        priority=priority,
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
    if recipient_type == "user":
        _mirror_to_super_admins(db, notification)
    return notification


# ZR-61: super admins are read-only overseers of what happens between hosts and
# renters, but only ever heard about applications, listings and a few disputes --
# so their "Tenancies & sublets", "Payments" and "Disputes & safety" notices
# never arrived. Every such notice a user receives is therefore also raised for
# the super admins, naming who it went to. LEASING is already covered by its own
# admin notices; identity / authority / property verification results are
# private to the user and are not mirrored.
_OVERSIGHT_LABEL = {"OCCUPANCY": "Tenancy", "PAYMENTS": "Payment", "DISPUTES_AND_SAFETY": "Dispute & safety"}
_OVERSIGHT_SAFETY_PREFIXES = (
    "dispute", "dispute_case", "dispute_claim", "dispute_settlement", "habitability_incident", "screening_check",
)


def _mirror_to_super_admins(db: Session, notification: Notification) -> None:
    prefix = notification.notification_type.split(".", 1)[0]
    category = notification.category
    if category not in _OVERSIGHT_LABEL or notification.notification_type.endswith("_admin"):
        return
    if category == "DISPUTES_AND_SAFETY" and prefix not in _OVERSIGHT_SAFETY_PREFIXES:
        return
    try:
        user = db.get(UserAccount, notification.recipient_user_id)
        who = f"{user.full_name or user.email} ({user.email})" if user else "a user"
        notify_all_super_admins(
            db, title=f"{_OVERSIGHT_LABEL[category]}: {notification.title}"[:255],
            message=f"Sent to {who}: {notification.message}"[:2000],
            notification_type=notification.notification_type,
            related_entity_type=notification.related_entity_type, related_entity_id=notification.related_entity_id,
        )
    except Exception:
        logger.exception("notification: could not raise the admin oversight copy (type=%s)", notification.notification_type)


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
    them (every active account on the party). No-ops silently if the party has no user account (e.g. it's an
    admin-owned party, which has nobody on the USER side to notify)."""
    if not party_id:
        return None
    # Everyone on the party hears about it, not just the first account found --
    # a host's co-managers act for the same party. Returns the first notice made.
    users = db.scalars(
        select(UserAccount).where(UserAccount.party_id == party_id, UserAccount.is_active.is_(True)).order_by(UserAccount.id)
    ).all()
    first = None
    for user in users:
        sent = notify_user(db, user.id, **kwargs)
        first = first or sent
    return first


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


def _get_or_create_preference(db: Session, *, recipient_type: str, recipient_user_id: int | None, recipient_admin_id: int | None) -> NotificationPreference:
    recipient_key = f"user:{recipient_user_id}" if recipient_type == "user" else f"admin:{recipient_admin_id}"
    pref = db.scalar(select(NotificationPreference).where(NotificationPreference.recipient_key == recipient_key))
    if pref is not None:
        return pref
    pref = NotificationPreference(
        recipient_type=recipient_type, recipient_user_id=recipient_user_id, recipient_admin_id=recipient_admin_id,
        recipient_key=recipient_key,
    )
    db.add(pref)
    db.commit()
    db.refresh(pref)
    return pref


def get_or_create_preference_for_user(db: Session, user_id: int) -> NotificationPreference:
    return _get_or_create_preference(db, recipient_type="user", recipient_user_id=user_id, recipient_admin_id=None)


def get_or_create_preference_for_admin(db: Session, admin_id: int) -> NotificationPreference:
    return _get_or_create_preference(db, recipient_type="admin", recipient_user_id=None, recipient_admin_id=admin_id)


def update_preference(
    db: Session, pref: NotificationPreference, *,
    opted_out_categories: list[str], quiet_hours_enabled: bool, quiet_hours_start_minute: int, quiet_hours_end_minute: int,
) -> NotificationPreference:
    """Section 11 gap: NOTIFICATION_OPTABLE_CATEGORIES is the whitelist --
    DISPUTES_AND_SAFETY (or any future non-optable category) is silently
    dropped from whatever the caller sent rather than rejected outright,
    same fail-closed-but-forgiving posture as other preference-shaped
    settings in this codebase."""
    from app.models.notification import NOTIFICATION_OPTABLE_CATEGORIES

    pref.opted_out_categories = [c for c in opted_out_categories if c in NOTIFICATION_OPTABLE_CATEGORIES]
    pref.quiet_hours_enabled = quiet_hours_enabled
    pref.quiet_hours_start_minute = max(0, min(1439, quiet_hours_start_minute))
    pref.quiet_hours_end_minute = max(0, min(1439, quiet_hours_end_minute))
    db.commit()
    db.refresh(pref)
    return pref
