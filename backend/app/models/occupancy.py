from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint
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
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING_MOVE_IN")
    move_in_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # ZR-ENG-CLR-004 AC-20/10.2: these three are legally distinct dates that
    # can differ ("final occupancy date, rent liability end date and
    # physical move-out date may differ and must be separately stored") --
    # move_out_date stays the one "when did they physically leave" date
    # end_occupancy always sets; the other two are optional refinements a
    # caller can supply when notice/liability actually diverge from that
    # (see crud/occupancy.py:end_occupancy).
    notice_given_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    liability_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    termination_effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    move_out_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    offer: Mapped["Offer"] = relationship()
    listing: Mapped["Listing"] = relationship()
    room: Mapped["Room"] = relationship()
    guest: Mapped["Guest"] = relationship()
    obligations: Mapped[list["Obligation"]] = relationship(back_populates="occupancy")
    co_tenants: Mapped[list["OccupancyCoTenant"]] = relationship(back_populates="occupancy", cascade="all, delete-orphan")


class OccupancyCoTenant(Base):
    """ZR-ENG-CLR-006 AC-25: 'Joint-tenancy ... relationships route through
    Section 3/4 relationship data instead of assuming one renter = one
    agreement.' No such relationship existed anywhere in this codebase --
    Occupancy.guest_id is the sole tenant of record on every occupancy this
    build has ever created. This is the minimal Section 3/4 fact this
    build actually needs to make that assumption checkable: an occupancy
    with one or more rows here has additional co-tenants beyond its own
    guest_id, and crud/termination.py routes a termination case for such an
    occupancy to PENDING_REVIEW rather than auto-resolving an effective
    date/liability outcome no jurisdiction-general rule exists for here (a
    single joint tenant's own notice may end the whole tenancy, only their
    own interest, or neither, depending on jurisdiction and agreement type
    -- AC-35's own fail-closed doctrine, applied to this fact instead of a
    cause code)."""

    __tablename__ = "occupancy_co_tenants"
    __table_args__ = (UniqueConstraint("occupancy_id", "guest_id", name="uq_occupancy_co_tenants_occupancy_guest"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    occupancy_id: Mapped[int] = mapped_column(ForeignKey("occupancies.id", ondelete="CASCADE"), nullable=False, index=True)
    guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    added_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    occupancy: Mapped["Occupancy"] = relationship(back_populates="co_tenants")
    guest: Mapped["Guest"] = relationship()
