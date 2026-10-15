"""ZR-ENG-CLR-006 Section 9: 'Property unavailability after move-in requires
a dedicated incident path.' H2/H3 severities reuse Room.status ("active"/
"inactive") -- already the sole mechanism this codebase has for 'freeze new
booking' (crud/listing.py:is_listing_available/annotate_availability both
already refuse an inactive room) -- rather than inventing a second,
parallel inventory-block system. Nothing here touches the active
Occupancy itself; ending the tenancy (if warranted) goes through the
existing termination_case PROPERTY_UNINHABITABLE/HOST_FAULT_OR_NONPERFORMANCE
pathway (crud/termination.py) as its own, separate action."""

from datetime import datetime, timezone

from sqlalchemy import Integer, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-006 Section 9.1's own severity table.
HABITABILITY_SEVERITIES = ("H0", "H1", "H2", "H3")
# H2 (uninhabitable/unavailable) and H3 (emergency danger) are the two
# severities the spec requires to "freeze new booking" -- see
# crud/habitability_incident.py:report_habitability_incident.
ROOM_FREEZING_SEVERITIES = ("H2", "H3")
# OPEN -> ACKNOWLEDGED (host) -> FIX_REPORTED (host) -> RESOLVED (renter
# confirms, or is deemed to after the confirm window). The renter rejecting a
# reported fix sends it back to ACKNOWLEDGED. No admin step.
HABITABILITY_INCIDENT_STATUSES = ("OPEN", "ACKNOWLEDGED", "FIX_REPORTED", "RESOLVED")
OPEN_HABITABILITY_STATUSES = ("OPEN", "ACKNOWLEDGED", "FIX_REPORTED")


class HabitabilityIncident(Base):
    __tablename__ = "habitability_incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False)
    reported_by_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    reported_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    severity: Mapped[str] = mapped_column(String(2), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(String(2000), default="")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the fix is due, by severity (crud/habitability_incident.py:_FIX_DAYS).
    fix_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fix_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fix_rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    # RENTER_CONFIRMED | DEEMED_CONFIRMED (no reply in the confirm window).
    resolution_basis: Mapped[str] = mapped_column(String(30), default="")
    # ZR-ENG-CLR-006 Section 9.1 H1 row: "Compliance review; possible rent
    # adjustment/credit" -- "possible" means discretionary, not a computed
    # abatement percentage this build doesn't have. An admin applies a
    # specific amount against a specific paid RENT obligation for this same
    # occupancy (crud/habitability_incident.py:apply_habitability_credit),
    # executed as a real Section 5 RefundRequest -- one-shot per incident,
    # same "surface a real amount, don't fabricate a formula" discipline as
    # crud/termination.py:set_tribunal_liability.
    credited_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0.0)
    credited_refund_request_id: Mapped[int | None] = mapped_column(ForeignKey("refund_requests.id"), nullable=True)
    credit_reason: Mapped[str] = mapped_column(String(2000), default="")
    credited_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    room: Mapped["Room"] = relationship()
    occupancy: Mapped["Occupancy"] = relationship()
