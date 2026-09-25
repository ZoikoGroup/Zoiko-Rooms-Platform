from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Exactly one of recipient_user_id / recipient_admin_id is set per row -- a
# notification always targets one concrete recipient. "Notify all super admins"
# events fan out into one row per super admin at creation time rather than a
# broadcast row read at query time, so each recipient's read state is independent
# and the read/list queries stay a single indexed lookup.
RECIPIENT_TYPES = ("user", "admin")

# Section 11 gap: a small, user-meaningful grouping over the ~20 dot-namespaced
# notification_type prefixes already in use across crud/ (see
# crud/notification.py:category_for_notification_type) -- one toggle per raw
# prefix would be an unusable settings page. DISPUTES_AND_SAFETY is
# deliberately excluded from NOTIFICATION_OPTABLE_CATEGORIES below: a dispute,
# habitability, identity-fraud, or adverse-screening notification is never
# something a user should be able to silence entirely, same "cannot bypass a
# safety control" doctrine as services/dispute_rbac.py's own AC-7.
NOTIFICATION_CATEGORIES = ("PAYMENTS", "LEASING", "OCCUPANCY", "DISPUTES_AND_SAFETY")
NOTIFICATION_OPTABLE_CATEGORIES = ("PAYMENTS", "LEASING", "OCCUPANCY")
NOTIFICATION_PRIORITIES = ("NORMAL", "HIGH")

_CATEGORY_BY_TYPE_PREFIX = {
    "agreement": "LEASING", "application": "LEASING", "offer": "LEASING", "listing": "LEASING",
    "payment": "PAYMENTS", "payout": "PAYMENTS", "refund": "PAYMENTS", "refund_entitlement": "PAYMENTS",
    "deposit": "PAYMENTS", "deposit_claim": "PAYMENTS",
    "occupancy": "OCCUPANCY", "termination_case": "OCCUPANCY", "booking_change_request": "OCCUPANCY",
    "host_entry_visit": "OCCUPANCY", "sublet_request": "OCCUPANCY",
    "dispute": "DISPUTES_AND_SAFETY", "dispute_case": "DISPUTES_AND_SAFETY", "dispute_claim": "DISPUTES_AND_SAFETY",
    "dispute_settlement": "DISPUTES_AND_SAFETY", "habitability_incident": "DISPUTES_AND_SAFETY",
    "identity_verification": "DISPUTES_AND_SAFETY", "screening_check": "DISPUTES_AND_SAFETY",
}
# Section 11 gap: quiet hours only ever suppress a NORMAL-priority
# notification (see crud/notification.py:_create) -- a HIGH-priority one
# always still gets created, same "real, auditable emergency exception"
# doctrine as models/host_entry_visit.py's own is_emergency bypass.
_HIGH_PRIORITY_TYPE_PREFIXES = (
    "dispute", "dispute_case", "dispute_claim", "dispute_settlement", "habitability_incident", "screening_check",
)


def category_for_notification_type(notification_type: str) -> str:
    prefix = notification_type.split(".", 1)[0]
    # An unmapped/future prefix falls to OCCUPANCY rather than raising --
    # same "never let a notification-layer gap break the caller's own
    # transaction" doctrine as crud/notification.py's own _never_raises.
    return _CATEGORY_BY_TYPE_PREFIX.get(prefix, "OCCUPANCY")


def priority_for_notification_type(notification_type: str) -> str:
    prefix = notification_type.split(".", 1)[0]
    return "HIGH" if prefix in _HIGH_PRIORITY_TYPE_PREFIXES else "NORMAL"


class Notification(Base):
    __tablename__ = "notifications"
    # Prevents the exact same event from notifying the exact same recipient twice
    # (e.g. a double-submitted approve click). Postgres unique constraints never
    # treat two NULLs as equal, so a constraint built directly on the two nullable
    # recipient_user_id/recipient_admin_id columns would silently never fire --
    # exactly one of them is always NULL. recipient_key is a non-nullable
    # "user:<id>" / "admin:<id>" string computed at insert time (see
    # crud/notification.py) specifically so this constraint has no NULL column to
    # dodge past.
    __table_args__ = (
        UniqueConstraint(
            "notification_type", "related_entity_type", "related_entity_id", "recipient_key",
            name="uq_notifications_dedupe",
        ),
        Index("ix_notifications_recipient_user", "recipient_user_id", "is_read"),
        Index("ix_notifications_recipient_admin", "recipient_admin_id", "is_read"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_type: Mapped[str] = mapped_column(String(10), nullable=False)
    recipient_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=True
    )
    recipient_admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=True
    )
    recipient_key: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(String(2000), default="")
    notification_type: Mapped[str] = mapped_column(String(100), nullable=False)
    related_entity_type: Mapped[str] = mapped_column(String(50), default="")
    related_entity_id: Mapped[str] = mapped_column(String(50), default="")
    category: Mapped[str] = mapped_column(String(30), default="OCCUPANCY")
    priority: Mapped[str] = mapped_column(String(10), default="NORMAL")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    recipient_user: Mapped["UserAccount"] = relationship()
    recipient_admin: Mapped["AdminUser"] = relationship()
