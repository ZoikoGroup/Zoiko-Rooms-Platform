from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Section 9 gap: "No rules/UI around host access, entry notice, or
# emergency entry" -- previously genuinely absent from this codebase, no
# model field, no policy config, no adjacent concept even inside
# habitability incidents. This is the real, auditable record of a Host's
# scheduled visit to an occupied unit, plus the jurisdiction-configured
# notice period it must respect (MarketPolicyPack.entry_notice_hours).
ENTRY_VISIT_PURPOSES = ("INSPECTION", "REPAIR", "SHOWING", "OTHER")
ENTRY_VISIT_STATUSES = ("SCHEDULED", "COMPLETED", "CANCELLED")


class HostEntryVisit(Base):
    """One Host-initiated visit to an occupied room. Requires the
    jurisdiction's own entry_notice_hours between scheduling and the visit
    itself, unless is_emergency is true -- a real, auditable exception
    (fire, flood, gas leak, etc.), never a silent bypass: emergency_reason
    is required whenever is_emergency is set (see
    crud/host_entry_visit.py:schedule_entry_visit)."""

    __tablename__ = "host_entry_visits"

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False, index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    scheduled_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), default="INSPECTION")
    notes: Mapped[str] = mapped_column(String(2000), default="")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_reason: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="SCHEDULED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    occupancy: Mapped["Occupancy"] = relationship()
    room: Mapped["Room"] = relationship()
    scheduled_by_admin: Mapped["AdminUser"] = relationship()
