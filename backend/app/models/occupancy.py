from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

OCCUPANCY_STATUSES = ("PENDING_MOVE_IN", "ACTIVE", "ENDED")


class Occupancy(Base):
    """Created once move-in eligibility passes (agreement signed, initial rent +
    deposit obligations paid). Recurring rent is not a background job -- no
    scheduler exists anywhere in this stack -- it's the idempotent function
    `generate_next_rent_obligation()` in crud/occupancy.py, triggered automatically
    right after the current period's rent obligation is marked paid, or manually via
    an admin action. `expected_end_date` bounds how far that generation can run
    without a lease renewal step."""

    __tablename__ = "occupancies"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"), unique=True, nullable=False)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING_MOVE_IN")
    move_in_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    move_out_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    requested_move_out_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    move_out_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    offer: Mapped["Offer"] = relationship()
    listing: Mapped["Listing"] = relationship()
    room: Mapped["Room"] = relationship()
    guest: Mapped["Guest"] = relationship()
    obligations: Mapped[list["Obligation"]] = relationship(back_populates="occupancy")
