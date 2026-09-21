from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Section 11 gap: previously no recipient had any way to opt out of a
# notification category or set quiet hours -- every notify_* call in
# crud/notification.py unconditionally created a row. One row per recipient
# (not per category, per models/notification.py's own NOTIFICATION_
# CATEGORIES docstring) -- opted_out_categories is a small JSON list, same
# idiom as TerminationCase.evidence_refs.
#
# quiet_hours_start_minute/end_minute are minutes-since-midnight (0-1439) in
# UTC -- this codebase has no per-user timezone field anywhere (UserAccount/
# AdminUser both checked), so this is an honest UTC-only wall-clock window,
# not a real per-user-local one. A window that wraps midnight is expressed
# as start > end (e.g. 22:00-07:00 -> start=1320, end=420) and is treated as
# start<=now<1440 OR 0<=now<end.
#
# Quiet hours can only ever suppress notification *creation* at the moment it
# would happen -- there is no scheduler anywhere in this codebase (see
# services/evidence_retention.py's own docstring for that same discipline)
# to defer a suppressed notification and deliver it once the window ends, so
# a NORMAL-priority notification raised during quiet hours is simply never
# created, not queued.
class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("recipient_key", name="uq_notification_preferences_recipient"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_type: Mapped[str] = mapped_column(String(10), nullable=False)
    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=True)
    recipient_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=True)
    recipient_key: Mapped[str] = mapped_column(String(20), nullable=False)
    opted_out_categories: Mapped[list] = mapped_column(JSON, default=list)
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_hours_start_minute: Mapped[int] = mapped_column(Integer, default=1320)
    quiet_hours_end_minute: Mapped[int] = mapped_column(Integer, default=420)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc),
    )

    recipient_user: Mapped["UserAccount"] = relationship()
    recipient_admin: Mapped["AdminUser"] = relationship()
