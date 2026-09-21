from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Section 9 gap: "No move-in/move-out condition report (photos, damage
# notes, etc.) anywhere" -- previously the only evidence-attachment
# capacity near an occupancy was OccupancyHandoverEvent's single
# evidence_ref/notes string per event type (one row per type, no room
# breakdown, no multi-photo trail) -- this is the real, structured,
# multi-item condition report that was missing. A deposit claim
# (crud/finance.py:submit_deposit_claim) still isn't required to reference
# one -- linking claims to a specific report item is a natural follow-up,
# not built here -- but the baseline evidence to compare against now
# actually exists.
CONDITION_REPORT_TYPES = ("MOVE_IN", "MOVE_OUT")
CONDITION_RATINGS = ("GOOD", "FAIR", "DAMAGED")


class OccupancyConditionReportItem(Base):
    """One room/area's condition record (an optional photo plus notes),
    for either the move-in or move-out side of one occupancy. Both the
    renter and the Host/admin may add items to either report_type -- unlike
    OccupancyHandoverEvent, this is deliberately many-rows-per-type, not a
    single immutable event, since a real condition report is a
    room-by-room walkthrough, not one fact."""

    __tablename__ = "occupancy_condition_report_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False, index=True)
    report_type: Mapped[str] = mapped_column(String(10), nullable=False)
    # Free text, not an enum -- no structured room-layout data exists
    # anywhere in this codebase (Room is one row per rentable room, not a
    # floor plan of sub-areas) for this to validate against.
    area: Mapped[str] = mapped_column(String(100), default="")
    condition_rating: Mapped[str | None] = mapped_column(String(10), nullable=True)
    notes: Mapped[str] = mapped_column(String(2000), default="")
    # Same storage convention as DisputeEvidenceItem -- null stored_filename
    # means a notes-only item, never an empty/broken file reference.
    stored_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(50), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    recorded_by_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    recorded_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    occupancy: Mapped["Occupancy"] = relationship()
    recorded_by_guest: Mapped["Guest"] = relationship()
    recorded_by_admin: Mapped["AdminUser"] = relationship()
